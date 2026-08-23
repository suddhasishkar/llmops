# AI System Card — Nimbus Telecom Support Copilot

## System Name
Nimbus Telecom Support Copilot

## Purpose
Deflect routine product, plan, billing, cancellation, and refund questions
from human support agents, grounded in current enterprise policy, with
every monetary or sensitive action routed through human approval.

## Intended Use
Answering product/plan/billing/cancellation/refund questions for
authenticated Nimbus customers via the support web/chat interface;
proposing (never executing) service credits; creating support tickets;
checking network outage status and account plan/bill details.

## Prohibited Use
Must not execute financial transactions of any kind. Must not be used for
account authentication/identity decisions. Must not be exposed to
unauthenticated users for account-specific queries. Must not act on any
account other than the authenticated session's own. Not evaluated or
approved for use outside the training/lab environment described in this
repository.

## Supported Users
Authenticated Nimbus Telecom customers (synthetic, lab-only identities);
tier-1 support agents as a co-pilot/reference tool.

## Components and Versions
| Component | Version | Last Changed | Owner |
|---|---|---|---|
| Application image | see `release_sha` tag | per release | AI/LLMOps team |
| Model deployment | real Microsoft Foundry (`AIServices`) `gpt-5-mini` deployment, GlobalStandard SKU (`azd`-provisioned; endpoint/deployment name in `azd env get-values`). No stub or offline model path exists in this codebase. One shared deployment serves all three agent roles. Migrated off `gpt-4o-mini` after its 2026-03-31 Standard-deployment retirement. | per model version bump | AI/LLMOps team |
| Agent | Manager/Specialist multi-agent split — `app/agent/manager_agent.py` (routing only, two functions, no tool access), `app/agent/billing_agent.py` / `app/agent/account_agent.py` (the two specialists, one real Foundry call per turn each, scoped to their own tools) — see `docs/adr/0001-foundry-and-multiagent.md` | 2026-08-20 | AgentOps/platform team |
| Prompt templates | `system_prompt_manager.md` (routing), `system_prompt_account.md`, `system_prompt_billing_baseline.md` (production), `system_prompt_billing_candidate_broken.md` / `system_prompt_billing_candidate_fixed.md` (Day 1 lab candidates) | 2026-08-20 | AI/LLMOps team |
| Agent policy / guardrails | `app/agent/tool_policy.py` (`agent_policy_layer()`, `enforce_tool_execution_boundary()`) — unchanged by, and applies identically regardless of, the multi-agent split above | 2026-08-25 (post-lab fix) | AgentOps/platform team |
| Retrieval index | Real Azure AI Search index only (`nimbus-knowledge-docs`, or `AZURE_SEARCH_INDEX` if overridden). No local index or fallback exists. Built/repaired by `scripts/build_search_index.py`; freshness verified by `app/retrieval/retrieval.check_freshness()` against live index state | per index build | Platform/MLOps (index infra) |
| Embedding model | None — retrieval is plain keyword (BM25) search via Azure AI Search's `search_text`, a deliberate, named scoping decision, not an oversight. Vector/hybrid search is a documented next step | n/a | Platform/MLOps |
| Content-safety guardrail | `app/agent/content_safety.py` — real Azure AI Content Safety `analyze_text()`, always called, no offline stub. Severity ≥2 flags a category | 2026-08-26 (post-incident) | Security/guardrail team |
| Tool schemas | `app/agent/tool_schemas.py::TOOLS_SCHEMA` (shared master list) plus `BILLING_TOOL_NAMES`/`ACCOUNT_TOOL_NAMES` per-specialist subsets and `MANAGER_ROUTING_SCHEMA`, checked by `tests/validate_tool_schemas.py` and `tests/unit/test_agent_structure.py` | 2026-08-20 | AgentOps/platform team |
| Identity / authentication | Caller-supplied `customer_id`, trusted as given. There is no bearer-token/JWT validation layer in this rebuild — a stated, deliberate simplification for this presentation-grade asset, not a hidden gap. See `Agent_End_to_End_Architecture.md` Section 7 | n/a | Security/guardrail team |
| Cost tracking | `app/agent/cost_tracking.py` — per-turn ESTIMATED token/cost (not billed usage; see module docstring). Note two real model calls now occur per turn (manager + specialist) — see ADR 0001's cost/latency trade-off note | 2026-08-26 | AI/LLMOps team |
| Evaluation dataset | `eval/datasets/*.jsonl` | 2026-08-25 | AI/LLMOps team |
| Code scanning | `scripts/mock_security_scan.py` — a clearly-labeled PLACEHOLDER stage in `.github/workflows/ai-release.yml`'s `code-scanning` job; not a real SAST/secrets/CVE scanner. See the script's docstring for the exact real-tool swap-in points (Semgrep/Gitleaks/Trivy/Checkov/SonarQube) | 2026-08-20 | Platform/MLOps |
| Infrastructure | `azure.yaml` + `infra/main.bicep` + `infra/resources.bicep` — Microsoft Foundry, Azure AI Search, Azure AI Content Safety, Container Apps, ACR, Log Analytics, Application Insights, one restart alert; three environments (`nimbus-dev`/`nimbus-staging`/`nimbus-production`), one shared Bicep template parameterized per environment — see `docs/adr/0001-foundry-and-multiagent.md` | 2026-08-20 | Platform/MLOps |

## Data and Knowledge Sources
`knowledge_docs/` — synthetic policy corpus only: `refund_policy_v1.md`
(superseded, kept as the historical/fault-injection source),
`refund_policy_v2.md` (current), `outage_and_credit_policy.md`,
`billing_dispute_policy.md`. No real customer, billing, or payment data
is used anywhere in this system. See `Dataset_and_Evaluation_Guide.md`
for the full front-matter schema and how `supersedes` determines what
should be live in the index at any given time.

## Evaluation Evidence
See `eval/results/` for the latest evaluation run and
`eval/results/decision.json` for the current release-policy decision.
Baseline vs. candidate comparison for the Day 1 incident: see
`Day1_Lab_Guide.md` Parts 2–4. All evaluation runs call real Azure
OpenAI, real Azure AI Search, and real Azure AI Content Safety — there is
no mocked or offline evaluation path in this repository.

## Operational Evidence
`scripts/verify_deployment.py` is the automated post-deploy smoke test
run after every `azd deploy` / CI deployment — see
`.github/workflows/ai-release.yml`'s `provision-and-deploy` job. Canary
history is not currently tracked (this rebuild deploys a single
production revision, not a canary split — see "Known Limitations"
below); deployment history is visible via GitHub Environment history on
the `production` environment.

## Safety Evidence
`eval/datasets/safety_regression.jsonl` and
`eval/datasets/prompt_injection.jsonl` results (`Day2_Lab_Guide.md` Part
6, "Guardrail mini-lab"). Zero critical safety failures required for
promotion — `release-policy.yaml`'s `critical_safety_failures.max: 0`,
`on_breach: reject` (zero-tolerance, no review path).

## Known Limitations
Answer quality depends on index freshness — see the `check_freshness()`
drift signal in `app/retrieval/retrieval.py`, and `Day2_Lab_Guide.md` for
the full stale-index incident this is designed to catch. Not evaluated
for languages other than English. No vector/hybrid retrieval is
implemented (keyword/BM25 only — a named scoping decision, see
`app/retrieval/retrieval.py`'s module docstring). No real user
authentication is enforced — `customer_id` is a caller-supplied, trusted
value; this system must not be exposed to untrusted callers. Cost
figures reported by `app/agent/cost_tracking.py` are estimates, not
reconciled billing data. `check_freshness()` compares document
*presence* against `knowledge_docs/` on disk, not chunk-level content
drift within a document that keeps the same `doc_id` — a document whose
`effective_date`/`policy_owner`/body changed without a `doc_id` bump
would not be flagged by this check. There is no canary/staged rollout in
this rebuild's Container App deployment (`activeRevisionsMode: Single`)
— a deliberate simplification for a 30-minute lab; a production
deployment should reintroduce a canary/traffic-split step before
promoting a new image to 100%.

## Human Oversight
All `request_customer_credit` calls create a `PENDING_APPROVAL` record
only; `app/agent/tool_policy.approve_credit_request()` is the only path
to a terminal (still simulated) approved state, and must be invoked by a
human approver, never by the agent itself.

## Owners
AI/LLMOps team (model, prompts, evaluation); AgentOps/platform team
(tools, permissions, agent policy); Platform/MLOps (retrieval/index
infrastructure, cloud infra); Security/guardrail team (input/output/tool-
boundary guardrails).

## Incident Escalation
See `03_Day2_Governance_Monitoring_LLMOps_AgentOps.md` Section 17
(Incident Response flow) and `Day2_Lab_Guide.md` Part 7 for the
incident-record template exercised against `INC-2026-014`.

## Rollback Procedure
Revert application image, prompt version, and tool-permission/guardrail
policy together via `azd deploy` (or the `provision-and-deploy` GitHub
Actions job) pointed at a prior release SHA. Since this rebuild's
Container App runs a single active revision (see "Known Limitations"),
rollback is a full cutover to the prior image, not a traffic-split
promotion — confirm with `scripts/verify_deployment.py` immediately after.

## Change History
| Date | Change | Approved By | Evaluation Evidence Link |
|---|---|---|---|
| 2026-08-25 | Initial baseline release | [trainer/lab role] | `eval/results/decision.json` (baseline) |
| 2026-08-25 | Candidate prompt HOLD, disambiguation fix applied, PROMOTE | [trainer/lab role] | `eval/results/decision.json` (post-fix) |
| 2026-08-26 | Stale refund-policy citation incident (INC-2026-014) diagnosed and fixed via real Azure AI Search re-sync | [trainer/lab role] | `eval/results/stale_info_eval.json` (before/after) |
| 2026-08-27 | Full rebuild: removed all local/offline backend fallbacks (`NIMBUS_MODEL_BACKEND`, `NIMBUS_RETRIEVAL_BACKEND`, `NIMBUS_SAFETY_BACKEND`); collapsed Manager/Specialist multi-agent split into one agent; migrated infra to an `azd` template; Day 2 stale-index lab redesigned against real Azure AI Search state instead of a local file | [trainer/lab role] | this document; `Agent_End_to_End_Architecture.md` |
