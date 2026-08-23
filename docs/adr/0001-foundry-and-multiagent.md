# ADR 0001 — Migrate to Microsoft Foundry, reintroduce Manager/Specialist multi-agent

**Status:** Accepted, in progress. **Date:** 2026-08-20.

## Context

The project was rebuilt as a single-agent, cloud-only, 30-minute-lab
system (see the earlier rebuild in this repo's history). The lab-time
constraint has since been dropped: the goal now is a production-realistic
reference asset for organizational/client use, with three explicit
requirements: (1) reintroduce a multi-agent (Manager/Specialist)
architecture, (2) use Microsoft Foundry rather than a bare Azure OpenAI
resource, and (3) keep cost to a practical minimum across three
presentation-grade environments (dev/staging/production).

## Research findings (verified via web search, cited)

**Naming.** The platform previously called "Azure AI Foundry" is now
referred to as **Microsoft Foundry** in current Microsoft material — the
managed multi-agent runtime is "Foundry Agent Service," and the
open-source orchestration SDK (the AutoGen/Semantic Kernel successor) is
"Microsoft Agent Framework." [Microsoft Foundry Blog — Agent Service at
Build 2026](https://devblogs.microsoft.com/foundry/agent-service-build2026/)

**Infrastructure shape, confirmed against the current Bicep template
reference.** A Foundry resource is a `Microsoft.CognitiveServices/accounts`
resource with `kind: 'AIServices'` and `properties.allowProjectManagement:
true`; a Foundry **project** is a child resource,
`Microsoft.CognitiveServices/accounts/projects` (stable API version
`2025-06-01` at time of writing, with an actively evolving preview
surface beyond that — see the open AVM-module issue tracking a newer "V2
RP structure"). Model deployments attach exactly the way they already do
in this repo today — `Microsoft.CognitiveServices/accounts/deployments`
— unchanged. [Microsoft Learn — accounts/projects template
reference](https://learn.microsoft.com/en-us/azure/templates/microsoft.cognitiveservices/2025-04-01-preview/accounts/projects);
[Azure/bicep-registry-modules issue #5319, Foundry Project Type V2
RP](https://github.com/Azure/bicep-registry-modules/issues/5319)

**Practical consequence for this repo:** the Foundry account exposes the
**same Azure-OpenAI-compatible inference endpoint** our code already
calls (`properties.endpoint` + a deployment name). `app/agent/azure_openai_client.py`
needs **zero code changes** — only the Bicep resource `kind` and the
added `projects` child resource change. This is a low-risk migration at
the code layer.

**Multi-agent orchestration options.** Microsoft Agent Framework
(PyPI: `agent-framework`) is the current, actively developed SDK for
this, supporting sequential, concurrent, handoff, and group-chat/Magentic
orchestration patterns, with a `HandoffBuilder` specifically for the
Manager/Specialist shape, and first-class deployment to Foundry-hosted
infrastructure via `FoundryChatClient`. [microsoft/agent-framework —
multi-agent orchestration
patterns](https://deepwiki.com/microsoft/agent-framework/4.3-multi-agent-orchestration-patterns);
[Microsoft Agent Framework 1.0
announcement](https://devblogs.microsoft.com/agent-framework/microsoft-agent-framework-version-1-0/)

## Decision

**Two-part decision, staged:**

1. **Now:** reintroduce the Manager/Specialist split using the same
   plain two-call function-calling pattern this codebase already proved
   out (a `route_decision()` call restricted to two routing functions,
   then a scoped specialist `decide()` call) — against a **Foundry**
   `AIServices` account + project, not a bare OpenAI-kind account. This
   is implementable today with code I can verify against this repo's
   existing, tested pattern, with no new SDK dependency and no
   preview-API guesswork.
2. **Documented upgrade path, not built yet:** migrate the manager/
   specialist orchestration itself onto Microsoft Agent Framework's
   `HandoffBuilder`, once its exact current API is verified directly
   against the installed package version (this document deliberately
   does **not** hand-write `HandoffBuilder` call signatures from search
   summaries alone — the source wasn't fetchable to confirm exact method
   names, and shipping guessed API calls in a client-facing asset is a
   worse outcome than a clearly-labeled follow-up task). Tracked as a
   follow-up ADR once `pip install agent-framework` and its bundled
   samples are available to verify against directly.

The deterministic two-layer guardrail (`app/agent/tool_policy.py`) is
**unchanged and non-negotiable** regardless of which orchestration layer
sits above it — this is true whether the code above it is our own
two-call pattern or a future `HandoffBuilder` workflow. Every specialist
still only ever receives a tool schema that has already survived
`agent_policy_layer()` + `content_safety.check_content()`, intersected
with that specialist's own scope, and every tool call still executes
through `enforce_tool_execution_boundary()`. Multi-agent routing changes
who decides which tool to call; it never changes what's allowed to
execute.

## Specialist split

- **ManagerAgent** — routing only, two functions (`delegate_to_billing`,
  `delegate_to_account`), never sees the real tool schemas, defaults to
  `account` (the specialist with no monetary tool) whenever the model's
  routing decision is unclear — a deliberate safety default carried over
  from this pattern's original design.
- **BillingAgent** — `retrieve_latest_bill`, `create_support_ticket`,
  `request_customer_credit`. This is where the Day 1 lab's disambiguation
  fault lives (`system_prompt_billing_baseline.md` vs.
  `system_prompt_billing_candidate_broken.md`).
- **AccountAgent** — `get_customer_plan`, `check_network_outage`,
  `create_support_ticket`. No monetary-capable tool at all.

One shared Foundry model deployment serves all three roles (different
system prompts, same underlying deployment) — this is the direct
cost-minimization decision: multi-agent routing adds a second real model
call per turn (manager + specialist, vs. one call in the prior
single-agent design), which is the real, honest cost/latency trade-off
of this architecture and worth stating plainly rather than hiding it.
`max_agent_steps` and the cost/latency story in `release-policy.yaml`
need revisiting for this reason — tracked in the roadmap's Phase C.

## Environment sizing (minimum-budget, presentation-grade)

| Resource | `nimbus-dev` | `nimbus-staging` | `nimbus-production` |
|---|---|---|---|
| Foundry `AIServices` account + model deployment | S0, capacity 1 (1K TPM) | S0, capacity 1 | S0, capacity 3 |
| Azure AI Search | **Free (F)** tier | Basic | Basic |
| Azure AI Content Safety | F0 (free) if quota permits — verify current free-tier limits before relying on this, not independently confirmed in this pass | S0 | S0 |
| Container Apps | Consumption plan, `minReplicas: 0` (scale to zero when idle) | `minReplicas: 0` | `minReplicas: 1` (only while actively presenting) |

**Why staging/production can't both be Free-tier Search:** Azure AI
Search allows only **one Free-tier service per subscription** — verified
against current Microsoft Learn docs. Dev gets the free one; staging and
production share the cheapest paid tier (Basic).

**The actual minimum-cost operating pattern, given "presentation-grade,
not real traffic":** don't run all three environments continuously.
`nimbus-dev` can stay up for ongoing iteration; bring `nimbus-staging`
and `nimbus-production` up with `azd up` before a demo/presentation and
`azd down --purge` after. Azure OpenAI/Foundry model billing is
consumption-based with no idle cost either way, so the real savings are
in Container Apps/Search/Content Safety sitting idle between
presentations.

## Consequences

- `infra/resources.bicep`'s OpenAI account changes `kind` from `'OpenAI'`
  to `'AIServices'`, gains `allowProjectManagement: true`, and gains a
  child `projects` resource. No other resource in the file changes shape.
- New files: `app/agent/manager_agent.py`, `app/agent/billing_agent.py`,
  `app/agent/account_agent.py`, plus split prompt files
  (`system_prompt_manager.md`, `system_prompt_billing_*.md`,
  `system_prompt_account.md`).
- `app/agent/support_agent.py` (the single-agent module) is retired —
  its `TOOLS_SCHEMA` moves to a shared `app/agent/tool_schemas.py` both
  specialists import from, avoiding duplicating the five tool
  definitions.
- `app/agent/agent.py`'s `model_tool_selection` span becomes two spans:
  `manager_route` then `specialist_tool_selection`.
- Cascading updates still required (tracked as immediate next steps, not
  done in this pass yet): `tests/validate_prompt_templates.py`,
  `tests/validate_tool_schemas.py`, `tests/unit/test_support_agent_structure.py`
  (renamed/rewritten for the new modules), `release-policy.yaml`
  (`max_agent_steps` headroom for a two-call minimum), `Day1_Lab_Guide.md`
  (the diagnosis step now diffs `system_prompt_billing_baseline.md` vs.
  `_candidate_broken.md`, not the single-agent prompt files),
  `Agent_End_to_End_Architecture.md`, `README.md`, `SYSTEM_CARD.md`.
