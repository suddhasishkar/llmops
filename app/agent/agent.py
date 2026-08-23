"""
Agent orchestration for the Nimbus Support Copilot -- Manager/Specialist
multi-agent architecture (see docs/adr/0001-foundry-and-multiagent.md).

Six real stages, always: retrieve -> agent-policy layer -> content-safety
check -> manager routing -> specialist tool decision -> deterministic
tool-execution boundary -> answer generation -> cost estimate. Every
stage that can call a cloud service, does, every time — there is no
NIMBUS_*_BACKEND environment variable left anywhere in this codebase to
flip. See `Agent_End_to_End_Architecture.md` Section 6 for the full
rationale behind removing the earlier dual-backend pattern.

The single `model_tool_selection` span from the earlier single-agent
build is now two spans: `manager_route` (which specialist handles this
turn) then `specialist_tool_selection` (that specialist's own real model
call, scoped to only its own tools). This is the direct, honest
cost/latency trade-off of reintroducing multi-agent routing -- two real
model calls per turn instead of one -- named plainly in ADR 0001 rather
than hidden.

Kept intentionally framework-free (plain Python, one file) so every line
here is readable in a short session regardless of which agent framework
you use day to day elsewhere. The deterministic two-layer guardrail
(`app/agent/tool_policy.py`) is unchanged and applies identically
regardless of which specialist ends up deciding — multi-agent routing
changes who decides which tool to call, never what's allowed to execute.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from opentelemetry import trace as otel_trace

from app.retrieval import retrieval
from app.agent import tool_policy, tools, content_safety, cost_tracking, audit_log
from app.agent import manager_agent, billing_agent, account_agent

REPO_ROOT = Path(__file__).resolve().parents[2]
TRACE_DIR = REPO_ROOT / "eval" / "traces"

# opentelemetry-api ships a no-op TracerProvider by default -- calling
# start_as_current_span() below is always safe and always free of cloud
# calls. It only starts exporting real spans to Application Insights once
# app/api/main.py's configure_azure_monitor() has installed a real SDK
# TracerProvider (i.e., only in a real deployment with
# APPLICATIONINSIGHTS_CONNECTION_STRING set).
_tracer = otel_trace.get_tracer("nimbus.agent")


@dataclass
class TraceSpan:
    name: str
    start_ts: float
    end_ts: float
    attributes: dict = field(default_factory=dict)


@dataclass
class Trace:
    trace_id: str
    release_sha: str
    prompt_version: str
    environment: str
    spans: list[TraceSpan] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "tags": {
                "release_sha": self.release_sha,
                "prompt_version": self.prompt_version,
                "environment": self.environment,
            },
            "spans": [
                {"name": s.name, "start_ts": s.start_ts, "end_ts": s.end_ts, "attributes": s.attributes}
                for s in self.spans
            ],
        }


def _generate_answer(user_message: str, retrieved_chunks: list[dict], tool_result: dict | None) -> dict:
    """Deliberately NOT a second model call. This composes the final
    answer text directly from whatever was actually retrieved and
    whatever the tool actually returned — a template, not free-form
    generation — so every citation is exact and there is no second
    hallucination surface between "the model decided a tool" and "the
    customer sees an answer." A production system might prefer a real
    generation call here for more natural phrasing; this program trades
    that for answers that are always exactly traceable to their source.
    """
    if not retrieved_chunks:
        return {"answer": "I don't have current policy information to answer that precisely — creating a ticket for a specialist to follow up.",
                "citations": []}
    top = retrieved_chunks[0]
    answer = f"{top['chunk'][:220].strip()} (Source: {top['doc_id']}, effective {top['effective_date']})"
    if tool_result:
        answer += f" | Tool result: {tool_result}"
    return {
        "answer": answer,
        "citations": [{"doc_id": c["doc_id"], "effective_date": c["effective_date"]} for c in retrieved_chunks],
    }


def run_turn(
    user_message: str,
    *,
    customer_id: str = "CUST-1002",
    prompt_version: str = "baseline",
    release_sha: str = "local-dev",
    environment: str = "lab",
    persist_trace: bool = True,
) -> dict:
    """Run one full agent turn and return a safe, summarized result plus a
    trace. Trace payloads are redacted summaries by design — raw
    free-text tool arguments longer than 200 chars are truncated, and no
    hidden reasoning is recorded, only the observable decision (which
    specialist was routed to, tool selected, arguments, outcome).

    `prompt_version` selects which BillingAgent prompt variant to load
    (`baseline` / `candidate_broken` / `candidate_fixed` -- see
    app/agent/billing_agent.py) and is ignored on a turn the manager
    routes to AccountAgent, which has only one prompt.

    `customer_id` is trusted exactly as given — there is no identity/JWT
    validation layer in this rebuild. That's a stated, deliberate
    simplification, not a hidden gap: see
    `Agent_End_to_End_Architecture.md` Section 7 for what a real
    deployment would add here and why it's out of scope for this asset
    (it needs a real Entra ID tenant this project doesn't provision).
    """
    trace = Trace(
        trace_id=f"trace-{uuid.uuid4().hex[:10]}",
        release_sha=release_sha,
        prompt_version=prompt_version,
        environment=environment,
    )

    def span(name, fn, **attrs):
        t0 = time.time()
        with _tracer.start_as_current_span(name) as otel_span:
            result = fn()
            t1 = time.time()
            span_attrs = attrs | {"result_summary": _summarize(result)}
            trace.spans.append(TraceSpan(name=name, start_ts=t0, end_ts=t1, attributes=span_attrs))
            otel_span.set_attribute("nimbus.release_sha", release_sha)
            otel_span.set_attribute("nimbus.prompt_version", prompt_version)
            otel_span.set_attribute("nimbus.environment", environment)
            otel_span.set_attribute("nimbus.trace_id", trace.trace_id)
            for k, v in span_attrs.items():
                otel_span.set_attribute(f"nimbus.{k}", str(v)[:200])
        return result

    # 1. retrieval query -- real Azure AI Search, always
    chunks = span("retrieval_query", lambda: retrieval.retrieve(user_message, k=3), query=user_message[:120])

    # 2. agent-policy layer (which tools are even offered this turn)
    policy = span("agent_policy_layer", lambda: tool_policy.agent_policy_layer(user_message, customer_id=customer_id))

    # 2.5 content-safety check -- real Azure AI Content Safety, always.
    # A flagged turn never gets to offer any tool to the model.
    safety = span("content_safety_check", lambda: content_safety.check_content(user_message))
    effective_allowed_tools = [] if safety.flagged else policy.allowed_tools
    if safety.flagged:
        audit_log.record_event(
            event_type="content_safety_blocked",
            customer_id=customer_id,
            agent_role="api",
            detail={"message_preview": user_message[:120]},
        )

    # 3. manager routing -- one real Foundry call, restricted to the two
    # delegate_to_* functions. The manager never sees the real tool
    # schemas and cannot call one under any circumstance.
    route = span(
        "manager_route",
        lambda: manager_agent.route(user_message),
        content_safety_flagged=safety.flagged,
    )

    # 4. specialist tool-selection decision -- a second real Foundry call,
    # scoped to only that specialist's own tools (intersected with
    # effective_allowed_tools from the policy/content-safety layers).
    if route == "billing":
        tool_call = span(
            "specialist_tool_selection",
            lambda: billing_agent.decide(user_message, customer_id, effective_allowed_tools, prompt_version=prompt_version),
            specialist="billing",
        )
    else:
        tool_call = span(
            "specialist_tool_selection",
            lambda: account_agent.decide(user_message, customer_id, effective_allowed_tools),
            specialist="account",
        )

    # 5. tool execution at the deterministic boundary
    tool_result = None
    tool_error = None
    step_count = 2
    if tool_call:
        step_count += 1
        try:
            tool_result = span(
                "tool_execution",
                lambda: tool_policy.enforce_tool_execution_boundary(
                    tool_call["name"], tool_call["arguments"], session_customer_id=customer_id, agent_role=route
                ),
                tool_name=tool_call["name"],
            )
        except (tools.ToolAuthorizationError, tools.ToolArgumentError) as e:
            tool_error = str(e)
            trace.spans.append(TraceSpan(name="tool_execution_blocked", start_ts=time.time(), end_ts=time.time(),
                                          attributes={"tool_name": tool_call["name"], "error": tool_error}))

    # 6. answer generation -- template composition, not a third model call
    generated = span("generate_answer", lambda: _generate_answer(user_message, chunks, tool_result))

    # 7. cost/token estimation -- see cost_tracking.py's module docstring.
    # Two real model calls happened this turn (manager_route +
    # specialist_tool_selection) -- the honest cost/latency trade-off of
    # this architecture vs. the earlier single-agent build's one call.
    cost_estimate = cost_tracking.estimate_turn_cost(user_message, generated["answer"])

    result = {
        "trace_id": trace.trace_id,
        "answer": generated["answer"],
        "citations": generated["citations"],
        "routed_specialist": route,
        "tool_call": tool_call,
        "tool_result": tool_result,
        "content_safety_flagged": safety.flagged,
        "content_safety_categories": safety.categories,
        "estimated_cost_usd": cost_estimate.estimated_cost_usd,
        "estimated_tokens": cost_estimate.estimated_input_tokens + cost_estimate.estimated_output_tokens,
        "tool_error": tool_error,
        "injection_flagged": policy.injection_flag,
        "step_count": step_count,
        "prompt_version": prompt_version,
    }

    if persist_trace:
        TRACE_DIR.mkdir(parents=True, exist_ok=True)
        (TRACE_DIR / f"{trace.trace_id}.json").write_text(json.dumps(trace.to_dict(), indent=2))

    return result


def _summarize(value, max_len: int = 200) -> str:
    """Safe summary for trace attributes — never logs raw hidden reasoning,
    only observable outcomes, and truncates any long payload.
    """
    text = json.dumps(value, default=str) if not isinstance(value, str) else value
    return text[:max_len] + ("..." if len(text) > max_len else "")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("message")
    parser.add_argument("--prompt-version", default="baseline", choices=sorted(billing_agent.PROMPT_VERSIONS))
    parser.add_argument("--customer-id", default="CUST-1002")
    args = parser.parse_args()
    out = run_turn(args.message, customer_id=args.customer_id, prompt_version=args.prompt_version)
    print(json.dumps(out, indent=2))
