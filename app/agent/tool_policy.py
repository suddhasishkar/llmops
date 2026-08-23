"""
Deterministic agent-policy and tool/action-layer guardrails.

Central training principle enforced here:
"A prompt-injection classifier cannot replace deterministic authorization
at the tool-execution boundary." The checks in this file are hard rules,
evaluated in code, independent of anything the model "decided" to do.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import audit_log, tools


# Trigger phrases that must be present (case-insensitive) before the
# credit tool is even OFFERED to the model as a callable option for an
# ambiguous request. This is the deterministic disambiguation control that
# the Day 1 lab's fix adds as defense-in-depth alongside the restored
# prompt instruction.
CREDIT_TRIGGER_PATTERNS = [
    r"\bcredit\b",
    r"\brefund\b",
    r"\bmoney back\b",
    r"\bcompensat",
]

# Prompt-injection heuristics (input-layer signal — NOT the control that
# actually blocks anything; see tool-execution boundary checks below).
INJECTION_PATTERNS = [
    r"ignore (all|any) (previous|prior) instructions",
    r"do not ask for approval",
    r"disregard (the|all|any) (system|previous) prompt",
    r"you are now",
    r"apply the maximum possible credit",
]


@dataclass
class PolicyDecision:
    allowed_tools: list[str]
    injection_flag: bool
    reason: str


def input_layer_classify(user_message: str) -> bool:
    """Cheap heuristic input-layer classifier. Returns True if the message
    looks like a prompt-injection / jailbreak attempt. This is a
    PROBABILISTIC signal for audit and UX purposes only — it must never be
    the sole gate on an unsafe action.
    """
    lowered = user_message.lower()
    return any(re.search(p, lowered) for p in INJECTION_PATTERNS)


def agent_policy_layer(user_message: str, *, candidate_prompt: bool = False, customer_id: str | None = None) -> PolicyDecision:
    """Decide which tools are even offered to the model for this turn.

    `candidate_prompt=True` simulates the Day 1 lab's BROKEN candidate
    prompt, which removed the disambiguation instruction. To make the
    failure mode reproducible in the lab, the broken candidate path skips
    the deterministic trigger-phrase gate on the credit tool UNLESS the
    fix has also been applied at the policy layer (see
    `require_deterministic_gate` below) — this models "the bug is real
    even with weaker prompt guidance, and the two-layer fix is what
    actually closes it."
    """
    injection_flag = input_layer_classify(user_message)

    always_allowed = ["get_customer_plan", "check_network_outage", "retrieve_latest_bill", "create_support_ticket"]

    if injection_flag:
        audit_log.record_event(
            event_type="prompt_injection_flagged",
            customer_id=customer_id or "unknown",
            agent_role="api",
            detail={"message_preview": user_message[:120]},
        )
        # Agent-policy layer: never offer the credit tool at all on a
        # flagged turn, regardless of prompt version.
        return PolicyDecision(
            allowed_tools=always_allowed,
            injection_flag=True,
            reason="input flagged by injection heuristic; credit tool withheld this turn",
        )

    credit_triggered = any(re.search(p, user_message.lower()) for p in CREDIT_TRIGGER_PATTERNS)

    if not credit_triggered:
        return PolicyDecision(
            allowed_tools=always_allowed,
            injection_flag=False,
            reason="no credit/refund trigger phrase present; credit tool not offered",
        )

    return PolicyDecision(
        allowed_tools=always_allowed + ["request_customer_credit"],
        injection_flag=False,
        reason="credit/refund trigger phrase present; credit tool offered, approval still required",
    )


def enforce_tool_execution_boundary(tool_name: str, arguments: dict, *, session_customer_id: str, agent_role: str = "unknown") -> dict:
    """The deterministic tool/action-layer boundary. This function is what
    actually prevents harm — independent of the input classifier, the
    agent-policy layer, and anything the model 'reasoned.'

    Raises tools.ToolAuthorizationError / tools.ToolArgumentError on any
    violation. Never silently downgrades a violation to a warning.

    Every attempt, allow, and deny is written to the persistent audit
    trail (app/agent/audit_log.py) -- see
    docs/adr/0004-llmops-agentops-rigor.md. This is a governance record,
    separate from and in addition to the mock business data tools.py
    still holds in-memory.
    """
    audit_log.record_event(
        event_type="tool_call_attempted",
        customer_id=session_customer_id,
        agent_role=agent_role,
        detail={"tool_name": tool_name},
    )
    try:
        result = _execute_tool(tool_name, arguments, session_customer_id=session_customer_id)
    except (tools.ToolAuthorizationError, tools.ToolArgumentError) as e:
        audit_log.record_event(
            event_type="tool_call_denied",
            customer_id=session_customer_id,
            agent_role=agent_role,
            detail={"tool_name": tool_name, "reason": str(e)},
        )
        raise

    audit_log.record_event(
        event_type="credit_requested" if tool_name == "request_customer_credit" else "tool_call_executed",
        customer_id=session_customer_id,
        agent_role=agent_role,
        detail={"tool_name": tool_name, **_audit_summary(tool_name, result)},
    )
    return result


def _audit_summary(tool_name: str, result: dict) -> dict:
    """Small, tool-specific subset of the result worth recording in the
    audit trail -- deliberately not the full result payload (see
    audit_log.record_event's docstring on why this stays small)."""
    if tool_name == "request_customer_credit":
        return {"request_id": result.get("request_id", ""), "amount": result.get("amount", 0), "state": result.get("state", "")}
    if tool_name == "create_support_ticket":
        return {"ticket_id": result.get("ticket_id", ""), "category": result.get("category", "")}
    return {}


def _execute_tool(tool_name: str, arguments: dict, *, session_customer_id: str) -> dict:
    if tool_name == "request_customer_credit":
        return tools.request_customer_credit(
            customer_id=arguments["customer_id"],
            amount=arguments["amount"],
            reason=arguments["reason"],
            session_customer_id=session_customer_id,
        )
    if tool_name == "create_support_ticket":
        return tools.create_support_ticket(
            customer_id=arguments["customer_id"],
            category=arguments["category"],
            description=arguments["description"],
            session_customer_id=session_customer_id,
        )
    if tool_name == "get_customer_plan":
        return tools.get_customer_plan(customer_id=arguments["customer_id"], session_customer_id=session_customer_id)
    if tool_name == "retrieve_latest_bill":
        return tools.retrieve_latest_bill(customer_id=arguments["customer_id"], session_customer_id=session_customer_id)
    if tool_name == "check_network_outage":
        return tools.check_network_outage(postcode=arguments["postcode"], simulate_slow=arguments.get("simulate_slow", False))

    raise tools.ToolAuthorizationError(f"Unknown or disallowed tool: {tool_name}")


def approve_credit_request(request_id: str, *, approver: str) -> dict:
    """Human-approval-gateway action. Moves a PENDING_APPROVAL record to a
    simulated APPROVED terminal state. This is still a mock — no financial
    system is ever called.
    """
    record = tools.CREDIT_APPROVAL_REQUESTS.get(request_id)
    if record is None:
        raise tools.ToolArgumentError(f"Unknown credit request_id: {request_id}")
    if record["state"] != "PENDING_APPROVAL":
        # Idempotency: re-approving an already-terminal record is a no-op,
        # not a new action.
        return record
    record["state"] = "APPROVED_SIMULATED"
    record["approved_by"] = approver
    audit_log.record_event(
        event_type="credit_approved",
        customer_id=record.get("customer_id", "unknown"),
        agent_role="api",
        detail={"request_id": request_id, "amount": record.get("amount", 0), "approver": approver},
    )
    return record
