# nimbus-support-copilot — Lab Repository Blueprint

Companion repository for the two-day training program *"From DevOps to
Production AI Engineering."* This is the runnable artifact behind both
primary labs — see `Day1_Lab_Guide.md` and `Day2_Lab_Guide.md` for the
full, copy-paste, step-by-step program.

**Everything here is synthetic.** No real customer, billing, payment, or
identity system is connected anywhere in this repository, on any cloud.
`request_customer_credit` only ever creates a mock `PENDING_APPROVAL`
record — there is no code path to an actual financial transaction.

**Cloud-only, deliberately, with no exceptions.** Microsoft Foundry, Azure AI
Search, and Azure AI Content Safety are all REQUIRED. There is no
`NIMBUS_MODEL_BACKEND` / `NIMBUS_RETRIEVAL_BACKEND` / `NIMBUS_SAFETY_BACKEND`
environment variable anywhere in this codebase — earlier versions of this
project had a free/local default for each of those three services and
treated the real Azure call as opt-in, which meant the "cloud" path was
never actually the default no matter what documentation said. This
rebuild deletes the local/stub code paths entirely rather than just
flipping their default, so there is nothing left to silently fall back
to. See `Agent_End_to_End_Architecture.md` Section 2 for the full
rationale.

**See also:** `Day1_Lab_Guide.md` and `Day2_Lab_Guide.md` for the full
hands-on program (cloud provisioning via `azd up`, the CI/CD pipeline,
and both incident labs, run end to end); `Agent_End_to_End_Architecture.md`
for the whole system's request lifecycle and component map;
`Dataset_and_Evaluation_Guide.md` for exactly what every evaluation
dataset and knowledge document contains and how it drives the release
gate. 

## Quick start

There is no "no cloud required" quick start anymore — every path through
this agent calls a real Azure service. What you *can* do without a cloud
subscription is validate the repo's structure (datasets, prompts, tool
schemas) and run the unit tests, which is exactly what
`scripts/seed_lab.sh` does:

```bash
python3 -m venv .venv && source .venv/bin/activate
bash scripts/seed_lab.sh
```

The very next thing that script tells you to run is `azd up` — see
`Day1_Lab_Guide.md` Part 0 for the complete one-time cloud setup
(prerequisites, `azd up`, loading its output into your shell, and a
scripted end-to-end verification), and Parts 1–5 for the timed, ~30-minute
in-class lab that follows it.

## Try the agent directly

Once `azd up` has run and you've loaded its output (`source <(azd env
get-values)` — see `Day1_Lab_Guide.md` Part 0.7):

```bash
python -m app.agent.agent "What is the cancellation window?"
python -m app.agent.agent "My bill seems wrong, can I get some money back?" --prompt-version baseline
python -m app.agent.agent "My bill seems wrong, can I get some money back?" --prompt-version candidate_broken
```

The last two commands reproduce the Day 1 lab's core regression: the
`baseline` prompt correctly creates a support ticket for the ambiguous
billing complaint; `candidate_broken` — missing one disambiguation
sentence — tends to call `request_customer_credit` instead. Run the
trajectory evaluator against the real dataset to see it scored, not
eyeballed:

```bash
python -m eval.run_agent_trajectory_eval --dataset eval/datasets/tool_trajectory_eval.jsonl --prompt-version baseline --out eval/results/agent_eval.json
python -m eval.run_agent_trajectory_eval --dataset eval/datasets/tool_trajectory_eval.jsonl --prompt-version candidate_broken --out eval/results/agent_eval.json
python -m eval.run_agent_trajectory_eval --dataset eval/datasets/tool_trajectory_eval.jsonl --prompt-version candidate_fixed --out eval/results/agent_eval.json
```

## Run the full release-policy gate

```bash
python -m eval.run_llm_eval --dataset eval/datasets/rag_eval.jsonl --prompt-version candidate_broken --out eval/results/llm_eval.json
python -m eval.run_agent_trajectory_eval --dataset eval/datasets/tool_trajectory_eval.jsonl --prompt-version candidate_broken --out eval/results/agent_eval.json
python -m eval.run_safety_regression --dataset eval/datasets/safety_regression.jsonl --dataset eval/datasets/prompt_injection.jsonl --out eval/results/safety_eval.json
python -m eval.apply_release_policy --policy release-policy.yaml --results eval/results/llm_eval.json eval/results/agent_eval.json eval/results/safety_eval.json --out eval/results/decision.json
python -m eval.post_pr_summary --decision eval/results/decision.json
```

## Reproduce the Day 2 stale-index incident

```bash
python -m scripts.inject_stale_doc                 # mutates the REAL Azure AI Search index
python -m eval.check_index_freshness                # reports drift, against live index state
python -m eval.run_llm_eval --dataset eval/datasets/stale_info_regression.jsonl --out eval/results/stale_info_eval.json
# then repair it:
python -m scripts.build_search_index                # re-syncs the live index to knowledge_docs/ on disk
python -m eval.run_llm_eval --dataset eval/datasets/stale_info_regression.jsonl --out eval/results/stale_info_eval.json
```

Full walkthrough, with expected output at every step, in
`Day2_Lab_Guide.md`.

## Deploy to Azure — `azd up`, one command

This project uses the Azure Developer CLI (`azd`), not a hand-assembled
sequence of `az` commands. `azure.yaml` plus `infra/main.bicep` +
`infra/resources.bicep` is the entire infrastructure definition.

```bash
azd auth login
azd env new nimbus-lab
azd up
```

`azd up` provisions every resource this app needs (a Microsoft Foundry
account + project + `gpt-5-mini` deployment, a LiteLLM gateway Container
App every agent role calls instead of Foundry directly -- see
`docs/adr/0002-llm-gateway-and-observability.md` -- Azure AI Search,
Azure AI Content Safety, two Container Apps (`api` + `gateway`), Azure
Container Registry, Log Analytics, Application Insights, one restart
alert), then automatically runs a postprovision hook that builds and
seeds the real Azure AI Search index (`scripts/build_search_index.py`),
then builds both this repo's `Dockerfile` and `gateway/Dockerfile` and
deploys them to their Container Apps. One command, ~15–25 minutes on a
clean subscription, no manual `export` of any endpoint or key at any
point — every value the app needs is written into `.azure/<env>/.env`
automatically and loaded with `source <(azd env get-values)`.

Full prerequisites, exact commands, and troubleshooting: `Day1_Lab_Guide.md`
Part 0.

Verify a deployment end-to-end (real Foundry, real Search, real
Content Safety, real tool-execution boundary — one script, no manual
clicking through the portal):

```bash
python scripts/verify_deployment.py --url "$SERVICE_API_ENDPOINT_URL"
```

Tear everything down, no leftover spend:

```bash
azd down --purge
```

## CI/CD and cloud credentials

`.github/workflows/ai-release.yml` is what `azd pipeline config` wires up
automatically (OIDC federation, no long-lived secrets) plus this
project's own evaluation, release-gate, and three-environment deploy jobs
layered on top. Two different kinds of Azure credentials are used,
deliberately kept separate:

- **`deterministic-tests`** and **`code-scanning`** run entirely offline
  — unit tests, dataset validation, prompt/schema validation, the
  dependency-lock-freshness check, a blocking `pip-audit`, and a
  clearly-labeled placeholder security scan (`scripts/mock_security_scan.py`
  — not a real SAST/secrets/CVE scanner; see its own docstring for the
  real-tool swap-in points). No Azure credentials, free on every PR.
- **`cloud-eval`** makes real Foundry, Azure AI Search, and Azure AI
  Content Safety calls on every PR, against a **persistent shared eval
  environment** — not any of the three deployed environments below. This
  is the one job in the whole pipeline with a genuine, unavoidable
  per-run cost, so it's **opt-in**: it only runs if the repo variable
  `ENABLE_CLOUD_EVAL` is set to `true` (see `ai-release.yml`'s header
  comment). Left unset, `cloud-eval` shows as skipped, not failed, on
  every PR, and — since `release-gate` needs `cloud-eval` and each
  `deploy-*` job needs `release-gate` — that skip cascades cleanly
  through the rest of the pipeline with no further configuration
  (GitHub Actions' documented default: a job whose `needs` was skipped
  is itself skipped). `Day1_Lab_Guide.md`'s Part 0 explains why this is
  off by default for the training lab specifically: none of Part 5's
  required deliverables depend on it.

  To turn it on: stand this up once (an instructor/admin task, not a
  per-trainee one) — provision one more `azd env` (e.g.
  `azd env new nimbus-eval && azd up`) dedicated to CI, then add its
  values as repo secrets/variables:

  | Name | Kind | Value |
  |---|---|---|
  | `EVAL_AZURE_CLIENT_ID` / `EVAL_AZURE_TENANT_ID` / `EVAL_AZURE_SUBSCRIPTION_ID` | secret | An app registration with OIDC federation trusting `repo:<org>/<repo>:pull_request` (same pattern as below, its own federated credential; see `Day1_Lab_Guide.md` 0.9.b for the exact `az ad app create` / `az role assignment create` commands) |
  | `EVAL_AZURE_OPENAI_DEPLOYMENT`, `EVAL_AZURE_SEARCH_ENDPOINT`, `EVAL_AZURE_CONTENT_SAFETY_ENDPOINT`, `EVAL_LLM_GATEWAY_ENDPOINT`, `EVAL_AZURE_KEY_VAULT_NAME` | variable | `azd env get-values` from that eval environment (`AZURE_OPENAI_DEPLOYMENT`, `AZURE_SEARCH_ENDPOINT`, `AZURE_CONTENT_SAFETY_ENDPOINT`, `LLM_GATEWAY_ENDPOINT`, `AZURE_KEY_VAULT_NAME`) — no `EVAL_AZURE_OPENAI_ENDPOINT`, because nothing in this codebase ever reads that value; the agent talks to Foundry through the LiteLLM gateway only, never directly, so the gateway's own endpoint and key (fetched from Key Vault at run time) are what `cloud-eval` actually needs |
  | `ENABLE_CLOUD_EVAL` | variable | `true` — the last step, and the only one that actually turns spending on |

  There is no mocking of agent behavior anywhere in this pipeline when
  it runs — a manager-routing call, a specialist tool-decision call, a
  retrieval call, and a content-safety call all really happen on every
  PR against this shared environment. This is the accepted trade-off for
  removing every local/offline fallback from the application code
  itself: with `cloud-eval` on, CI costs real (small) Azure spend on
  every PR, in exchange for CI results that can never diverge from
  production behavior the way a stubbed pipeline's could — a trade-off
  worth making deliberately, per repo, not defaulting to on.
- **`deploy-dev` → `deploy-staging` → `deploy-production`** promote the
  same built image through three real `azd` environments
  (`nimbus-dev`/`nimbus-staging`/`nimbus-production`) on every merge to
  `main` — **all three** are gated behind a GitHub Environment with
  required reviewers, not just production. A merge to `main` queues
  `deploy-dev`, but it waits for an approval click before it provisions
  or updates anything billable; `deploy-staging` and `deploy-production`
  each wait for their own separate approval in turn, so at most one
  environment is ever mid-deploy and nothing spends Azure money without
  someone deliberately approving that specific promotion. This is a
  deliberate cost control for a training/lab setting — three real Azure
  environments (each with its own Foundry, Search, and Content Safety
  resources) would otherwise get provisioned back-to-back on every merge
  with no chance to stop between them. See `docs/adr/0001-foundry-and-multiagent.md`
  for why each environment is sized differently and why `nimbus-production`
  here is presentation-grade, not built to take real customer traffic.
All three `deploy-*` jobs use the standard `azd pipeline config`-managed
OIDC credentials (`AZURE_CLIENT_ID`/`AZURE_TENANT_ID`/
`AZURE_SUBSCRIPTION_ID` secrets, `AZURE_LOCATION` variable) and only run
on push to `main` — currently one shared app registration across all
three environments (a documented minimum-cost trade-off; see the
workflow file's header comment for the stricter per-environment-identity
alternative).

To wire the deploy side up automatically instead of by hand:

```bash
azd pipeline config
```

This creates the app registration, the federated credential, and the
repo secrets/variables for you, interactively.

## Repository structure

```
app/
  agent/           agent.py (6-stage orchestration: retrieve -> policy ->
                    content-safety -> manager routing -> specialist tool
                    decision -> tool execution -> answer generation ->
                    cost estimate), manager_agent.py (routing-only, no
                    tool access, two functions), billing_agent.py /
                    account_agent.py (the two specialists, one Foundry
                    call per turn each, scoped to their own tools --
                    see docs/adr/0001-foundry-and-multiagent.md),
                    tool_schemas.py (shared TOOLS_SCHEMA + per-specialist
                    subsets), prompt_loader.py (shared prompt-file
                    loader), azure_openai_client.py (real client factory,
                    talks to the LiteLLM gateway, not Foundry directly --
                    see docs/adr/0002-llm-gateway-and-observability.md),
                    content_safety.py (real Azure AI Content
                    Safety, no stub), tool_policy.py (deterministic
                    agent-policy + tool-execution-boundary guardrails,
                    unchanged regardless of orchestration layer),
                    tools.py (5 synthetic mock tools), cost_tracking.py
                    (per-turn token/cost estimate)
  prompts/         system_prompt_manager.md (routing), system_prompt_account.md,
                    system_prompt_billing_baseline.md,
                    system_prompt_billing_candidate_broken.md (the
                    injected disambiguation fault),
                    system_prompt_billing_candidate_fixed.md
  retrieval/       retrieval.py (real Azure AI Search only: retrieve(),
                    list_indexed_doc_ids(), check_freshness())
  api/             minimal FastAPI channel layer (/chat, /healthz,
                    /approvals/*), turns on real Application Insights
                    telemetry when deployed
knowledge_docs/    synthetic policy corpus (incl. refund policy v1/v2 --
                    see Dataset_and_Evaluation_Guide.md)
eval/
  datasets/        rag_eval, tool_trajectory_eval, safety_regression,
                    prompt_injection, stale_info_regression (all .jsonl)
  run_*.py         Layer 2-4 evaluation runners (real cloud calls)
  apply_release_policy.py / gate_or_fail.py / post_pr_summary.py
  check_index_freshness.py
tests/
  unit/            Layer 1 pytest suite -- structural/deterministic only
  validate_*.py    prompt-template and tool-schema validation
system_card/       SYSTEM_CARD.md (populated AI system-card template)
gateway/           litellm_config.yaml + Dockerfile -- the LiteLLM LLM
                    gateway every agent role calls instead of Foundry
                    directly (MIT license, also wires in Langfuse
                    tracing -- see docs/adr/0002-llm-gateway-and-observability.md)
infra/             main.bicep (azd subscription-scope entry point),
                    resources.bicep (the actual resources, incl. the
                    litellmGateway Container App), main.parameters.json
azure.yaml         azd manifest (two services: api + gateway, plus the
                    postprovision hook)
scripts/
  seed_lab.sh              free local checks, points you at `azd up`
  build_search_index.py    creates/repairs the real Search index
  inject_stale_doc.py      trainer-only: injects the Day 2 fault into real cloud state
  verify_deployment.py     automated post-deploy smoke test
  mock_security_scan.py    placeholder code-scanning stage (see its docstring)
docs/adr/0001-foundry-and-multiagent.md
docs/adr/0002-llm-gateway-and-observability.md
.github/workflows/ai-release.yml
release-policy.yaml
Dockerfile
requirements.in          human-edited dependency floors -- the SOURCE
requirements.txt         pip-compile generated, fully pinned lock file --
                          what's actually installed everywhere
```

## A note on the evaluation numbers

`eval/run_llm_eval.py` and `eval/run_agent_trajectory_eval.py` compute
metrics from structural signals (was a citation present, did it match
the expected document, did the tool call match the expected trajectory)
against the output of a REAL agent turn — real manager-routing and
specialist Foundry calls, real Search query, real Content Safety check.
The scoring logic is deterministic and free; the agent turn it scores is
not a stub. Because real model calls aren't perfectly repeatable turn to
turn, exact numbers can vary by a case or two between runs — the
qualitative story
(`baseline`/`candidate_fixed` → PROMOTE, `candidate_broken` → HOLD or
REJECT depending on whether that run's ambiguous-billing cases tipped
into an unauthorized-action count) is what the lab is teaching, not a
specific fourth decimal place. Run the commands above and read your own
`decision.json` rather than expecting it to match a number printed in
this file.
