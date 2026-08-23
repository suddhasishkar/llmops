# ADR 0004 — LLMOps/AgentOps rigor: audit trail, cost/latency gating, LLM-as-judge, human-approval UI, drift monitoring

**Status:** Accepted, in progress. **Date:** 2026-08-20.

## Context

Phase C of `Platform_Maturity_Roadmap.md`, run after Phase B (ADR 0003).
This closes gaps named as far back as the very first gap analysis in
this engagement and repeated in ADR 0001's Consequences section:
`app/agent/tools.py`'s approval state is in-memory only, `release-policy.yaml`
never gated on cost or latency despite `app/agent/cost_tracking.py`
computing a per-turn estimate, `eval/run_llm_eval.py`'s own docstring
names LLM-as-judge as the thing a real deployment would use instead of
its structural proxy, the approval flow had no UI, and nothing ran on a
schedule to catch drift between PRs.

## Decision

**1. Persistent audit trail** (`app/agent/audit_log.py` + Azure Table
Storage, `resources.bicep`'s `auditStorage`). Deliberately additive, not
a replacement for `tools.py`'s in-memory mock stores — this is the
governance record of *what happened*, not an attempt to make the
synthetic business data durable. Fail-safe by design: a write failure
here logs and continues, never blocks or crashes a chat turn. Verified
against the official `azure-data-tables` SDK docs
(`TableServiceClient(endpoint=..., credential=DefaultAzureCredential())`,
`create_table_if_not_exists`, `upsert_entity`, exact `PartitionKey`/`RowKey`
casing) and the confirmed `Storage Table Data Contributor` role GUID
(`0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3`), both cited in the code/Bicep
comments.

**Named, not solved:** `tools.py`'s `CREDIT_APPROVAL_REQUESTS`/`TICKETS`
dicts still reset on container restart or scale-to-zero
(`containerAppMinReplicas: 0` in dev/staging). The audit trail now
durably records that a request *was* made even if the in-memory record
resets — but the mock approval workflow itself can still "forget" a
still-pending request across a scale-to-zero event. Making the mock
business data itself durable would mean moving `tools.py`'s synthetic
stores to the same Table Storage account, which changes that module's
"in-memory, reset per process — this is a lab" design philosophy;
deliberately not done in this pass so as not to conflate "audit trail"
with "make the demo stateful," two different problems.

**2. Cost/latency gating.** `eval/run_agent_trajectory_eval.py` now
measures real wall-clock latency around each real agent turn and pulls
the per-turn cost estimate `agent.py` already computes, aggregating
`p95_latency_ms` (nearest-rank over the dataset) and
`avg_cost_per_turn_usd`. Both are now `release-policy.yaml` thresholds,
wired into `apply_release_policy.py`'s existing check list.

**3. LLM-as-judge evaluation** (`eval/run_llm_judge_eval.py`). A real,
separate model call — through the same LiteLLM gateway (ADR 0002) — asks
the model to grade its own answer's groundedness and helpfulness
against the retrieved context, replacing nothing but adding what
`run_llm_eval.py`'s own docstring already said was missing.
**Deliberately informational-only, not a release-policy.yaml gate in
this pass** — judge scores need a calibration history against known
good/bad cases before an organization should trust a threshold on them
(release-policy.yaml's own header note: "every organization must set
its own thresholds against its own risk appetite... and evaluator
calibration"). `eval/post_pr_summary.py` surfaces the scores on every PR
so a human sees the trend now; promoting this to a blocking gate is a
deliberate future decision, not an oversight.

**4. Human-approval UI** (`GET /approvals/ui` in `app/api/main.py`).
Server-rendered, no build step, no external CDN dependency, calls the
existing `/approvals/pending` and `/approvals/approve` JSON endpoints —
adds no new authorization logic of its own. **Named, not solved:** the
page has no auth of its own, consistent with this repo's stated
presentation-grade scope (same caveat as Foundry/Content Safety/gateway
public ingress in ADR 0001/0002/0003) — a real deployment must put a
real identity check in front of it, tracked as a Phase D item.

**5. Nightly drift monitoring**
(`.github/workflows/nightly-drift-monitor.yml`). Runs the same
real-cloud eval suite `cloud-eval` runs on every PR, on a daily
schedule, against the persistent shared eval environment — catches
regressions no PR would surface (upstream model behavior change, index
staleness with no code change). On a non-promote decision or a failed
freshness check, opens/comments on a single deduplicated GitHub issue
rather than failing a build nobody would see.

**6. Dataset growth.** 26 → 36 cases across all five datasets (+2
prompt_injection, +2 rag_eval, +2 safety_regression, +1
stale_info_regression, +3 tool_trajectory_eval). New cases specifically
target gaps the Manager/Specialist split (ADR 0001) opened up that the
original single-agent-era dataset never had to cover: routing-targeted
injection attempts, AccountAgent's lack of a monetary tool, and
cross-domain compound questions that could route to either specialist.
Still a modest, illustrative dataset for a training/reference asset, not
a statistically powered evaluation set — named honestly in
`eval/run_agent_trajectory_eval.py`'s own new latency-percentile comment
rather than overclaiming rigor a 36-case sample doesn't have.

## Consequences

- New files: `app/agent/audit_log.py`, `tests/unit/test_audit_log.py`,
  `eval/run_llm_judge_eval.py`, `.github/workflows/nightly-drift-monitor.yml`.
- Changed: `app/agent/tool_policy.py` (audit_log wiring),
  `app/agent/agent.py` (audit_log wiring + import), `app/api/main.py`
  (`/approvals/ui`), `infra/resources.bicep` (`auditStorage` +
  role assignment + env var), `requirements.in`/`requirements.txt`
  (`azure-data-tables`), `release-policy.yaml`, `eval/apply_release_policy.py`,
  `eval/run_agent_trajectory_eval.py`, `eval/post_pr_summary.py`,
  `.github/workflows/ai-release.yml`, all five `eval/datasets/*.jsonl`.
- Cascading doc updates still required (tracked, not done in this
  pass): `README.md`'s repository-structure and CI/CD sections,
  `Agent_End_to_End_Architecture.md`, `SYSTEM_CARD.md`,
  `Dataset_and_Evaluation_Guide.md` (dataset count, new eval suite),
  `Platform_Maturity_Roadmap.md`'s Phase C row.
- Cost impact: one new Standard_LRS storage account (near-zero at this
  data volume) per environment; the LLM-as-judge call adds one real
  model call per `rag_eval.jsonl` case on every PR and nightly run —
  small but real, and worth watching if the dataset grows much further.
