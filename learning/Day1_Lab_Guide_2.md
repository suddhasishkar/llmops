# Day 1 Lab Guide — Repair a Blocked AI Release
### Nimbus Support Copilot — CI/CD, MLOps/LLMOps/AgentOps

This guide is self-contained. You do not need to have read any other file
in this repository first, though `Agent_End_to_End_Architecture.md` and
`Dataset_and_Evaluation_Guide.md` are worth skimming afterward if you want
more depth on any single piece.

**What changed from earlier versions of this lab:** every Azure service
this project uses — Microsoft Foundry (model deployment), Azure AI
Search, Azure AI Content Safety — is now REQUIRED, always deployed,
always called. There is no `NIMBUS_MODEL_BACKEND` /
`NIMBUS_RETRIEVAL_BACKEND` / `NIMBUS_SAFETY_BACKEND` environment variable
anywhere in this codebase, because there is no local or stub code path
left for any of them to select. Every command in this guide either runs
entirely offline (dataset/schema validation, unit tests) or talks to a
real Azure resource — there is no third option, and nothing here can
"quietly fall back to local" no matter what you forget to set.

**Also new:** the agent is now a Manager/Specialist multi-agent split
(`manager_agent.py` routes, `billing_agent.py` / `account_agent.py`
decide and call tools), not a single agent — see
`docs/adr/0001-foundry-and-multiagent.md` for why. This lab's injected
fault lives specifically in the BillingAgent's prompt, since the fault
scenario (a billing/credit disambiguation regression) only ever reaches
BillingAgent. The pipeline this lab exercises now also promotes through
three environments (`nimbus-dev` → `nimbus-staging` → `nimbus-production`)
on merge to `main`, and runs a clearly-labeled placeholder security-scan
stage — see Part 4.3.

---

## Part 0 — One-time cloud setup (do this BEFORE Day 1 starts)

Do this the evening before, or first thing before the training session
begins. It is entirely automated — one command does the provisioning,
building, and deploying — but Azure resource creation and RBAC
propagation genuinely take 15–20 minutes of wall-clock time that would
eat most of an in-class lab if you started it live. Everything in "Part
1" onward assumes this is already done and takes the ~30 minutes it
promises.

### 0.1 — Prerequisites

You need, once, on your own machine or a training VM:

| Tool | Check with | Install if missing |
|---|---|---|
| Azure CLI | `az version` | `curl -L https://aka.ms/InstallAzureCLIDeb \| sudo bash` (Linux) or https://learn.microsoft.com/cli/azure/install-azure-cli |
| Azure Developer CLI (`azd`) | `azd version` (need 1.9+) | `curl -fsSL https://aka.ms/install-azd.sh \| bash` or https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd |
| Python 3.12 | `python3 --version` | https://www.python.org/downloads/ |
| Docker (or Podman) | `docker version` | `azd` shells out to it to build the container image locally before pushing to ACR — see the troubleshooting note in Part 1 if you'd rather build in the cloud instead |
| An Azure subscription with Owner or Contributor + User Access Administrator | `az account show` | Ask your Azure admin; the deployment creates role assignments, which needs more than plain Contributor |

You also need Azure OpenAI access enabled on your subscription (a
one-time approval Microsoft requires per subscription, separate from
resource creation) and available `gpt-4o-mini` capacity in your target
region. If you've never deployed Azure OpenAI on this subscription
before, request access at https://aka.ms/oai/access first — this can
take anywhere from minutes to a day or two to approve, so do it as early
as possible, well before the evening-before-class window.

### 0.2 — Get the repository

```bash
git clone <this-repo-url> nimbus-support-copilot
cd nimbus-support-copilot
```

### 0.3 — Run the free local checks first

This installs dependencies and validates every dataset, prompt file, and
tool schema in the repo — all offline, no Azure calls, no cost. Fix
anything this reports before spending a single dollar of cloud spend.

```bash
./scripts/seed_lab.sh
```

Expected output ends with:
```
All local checks passed. ... Next step:
  azd auth login
  azd up
```

Alternate approach:
```
python3 -m venv ~/.venvs/myproject
source ~/.venvs/myproject/bin/activate
pip install -r requirements.txt
```

If this fails, the error names the exact file and field that's wrong —
there is no cloud dependency in this step at all, so a failure here is
always a real bug in the repo state you have, not a flaky cloud call.

### 0.4 — Authenticate

```bash
az login
azd auth login
```

Both commands open a browser window for interactive sign-in. If you're on
a headless VM, use `az login --use-device-code` and `azd auth login
--use-device-code` instead.

### 0.5 — Create your azd environment

```bash
azd env new nimbus-lab
```

This just creates a local `.azure/nimbus-lab/` folder to hold your
deployment's configuration — it does not touch Azure yet. Everyone in the
class should pick their own environment name (append your initials, e.g.
`nimbus-lab-jsmith`) if you're deploying into a shared subscription, since
`environmentName` becomes part of every resource name and they must be
globally unique for the Foundry, Search, and Content Safety accounts.

**This is a separate, individual scratch environment from the pipeline's
own three environments** (`nimbus-dev`/`nimbus-staging`/`nimbus-production`
— see Part 4.3 and `docs/adr/0001-foundry-and-multiagent.md`). Those three
are what `.github/workflows/ai-release.yml` provisions and promotes
through automatically on a merge to `main`; `nimbus-lab[-initials]` here
is just your own sandbox for working through this guide by hand.

By default this deploys with cost-conscious sizing (Basic-tier Search,
Foundry capacity 1, Container App `minReplicas: 0`). The template
defaults to Basic rather than the cheaper Free tier specifically because
Azure AI Search only allows **one** Free-tier service per subscription —
if every trainee's lab environment defaulted to Free in a shared
subscription, only the first one would succeed. If you're working solo
in your own subscription, `azd env set SEARCH_SKU free` before `azd up`
is fine. See `infra/main.bicep`'s `searchSku` / `openAiCapacity` /
`containerAppMinReplicas` parameters for the full list.

### 0.5.5 — Set up Langfuse tracing (optional, do this *before* `azd up`)

The LLM gateway (`gateway/`, see `docs/adr/0002-llm-gateway-and-observability.md`)
can trace every model call — prompts, completions, tool calls, latency
per hop — to Langfuse. `azd up` will succeed either way, but if you skip
this step the gateway just runs with tracing silently off: no error, no
warning, just an empty Langfuse dashboard later when you go looking for
a trace. If you want tracing working for Day 2, do this now, not after:

1. Sign up for the free **Langfuse Cloud Hobby tier** at
   [cloud.langfuse.com](https://cloud.langfuse.com) — no card required,
   50k units/month, 30-day retention (see ADR 0002 for why the free
   tier was chosen over self-hosting).
2. Create a project, then generate a public/secret key pair from the
   project's API keys settings page.
3. Set both **before** running `azd up` in Part 0.6:
   ```bash
   azd env set LANGFUSE_PUBLIC_KEY pk-lf-...
   azd env set LANGFUSE_SECRET_KEY sk-lf-...
   ```

Skipping this entirely is fine too — nothing else in either lab depends
on Langfuse tracing being on.

### 0.6 — `azd up`

```bash
azd up
```

You'll be prompted for:
- **Azure Subscription** — pick the one with Azure OpenAI access approved.
- **Azure location** — pick a region with `gpt-4o-mini` capacity. As of
  this writing, `eastus2`, `swedencentral`, and `westus3` reliably have
  it; verify current availability for your subscription with:
  ```bash
  az cognitiveservices account list-skus --kind OpenAI --location <region> \
    --query "[?name=='S0']" -o table
  ```
  or check https://learn.microsoft.com/azure/ai-services/openai/concepts/models
  for the current region list before picking one blind.

`azd up` then does all of the following, automatically, in order, with no
further prompts:

1. **`azd provision`** — deploys `infra/main.bicep`, which deploys
   `infra/resources.bicep`: creates the resource group, Log Analytics
   workspace, Application Insights, Azure Container Registry, Container
   Apps environment, a user-assigned managed identity, a Microsoft
   Foundry account + project + `gpt-4o-mini` deployment (one shared
   deployment serves the Manager, BillingAgent, and AccountAgent — see
   `docs/adr/0001-foundry-and-multiagent.md`), the Azure AI Search
   service, the Azure AI Content Safety account, the LiteLLM Proxy
   gateway Container App every agent role calls instead of Foundry
   directly (`docs/adr/0002-llm-gateway-and-observability.md`), an Azure
   Key Vault holding the gateway's master key and your Langfuse secret
   key if you set one in Part 0.5.5 (`docs/adr/0005-key-vault-for-gateway-secrets.md`),
   all the role assignments that let the app's managed identity (and
   your own signed-in account) reach each service with no static API
   key, and the restart/error-rate/latency alert rules plus the
   synthetic availability test discussed in Day 2 Part 7
   (`docs/adr/0003-deployment-reliability-and-observability.md`). This
   stage takes roughly 10–18 minutes — the Foundry account and Azure AI
   Search account creation are the slowest parts.
2. **postprovision hook** (see `azure.yaml`) — installs Python
   dependencies and runs `python -m scripts.build_search_index`, which
   creates the Azure AI Search index schema and uploads all four
   `knowledge_docs/*.md` files into it. Takes a few seconds.
3. **`azd deploy`** — builds **two** Docker images (the `api` service
   from the root `Dockerfile`, and the `gateway` service from
   `gateway/Dockerfile` — see `azure.yaml`), pushes both to your new
   Azure Container Registry, and updates both Container Apps to run
   them. Takes 3–7 minutes depending on your network.

Total: expect 15–27 minutes for a completely clean subscription — a
couple minutes longer than earlier runs of this lab, since `azd
provision` now also creates the gateway Container App and Key Vault,
and `azd deploy` now builds two images instead of one. Go get a coffee;
there is nothing to babysit.

When it finishes, `azd up` prints a `SERVICE_API_ENDPOINT_URL` — save
that, you'll use it throughout both labs.

### 0.7 — Load the deployment's config into your shell

Every script and CLI command in both labs reads Azure endpoint values
(`LLM_GATEWAY_ENDPOINT`, `AZURE_SEARCH_ENDPOINT`,
`AZURE_CONTENT_SAFETY_ENDPOINT`, `AZURE_CLIENT_ID`, and more) from
environment variables. `azd up` already wrote all of them to
`.azure/nimbus-lab/.env` — load them into your current shell with:

```bash
set -a
source <(azd env get-values)
set +a
```

Run this once per new terminal session for the rest of both labs. If a
Python command later fails with `RuntimeError: LLM_GATEWAY_ENDPOINT is
not set`, this is the step you forgot to re-run in that terminal.

**One value is deliberately not in `.env`:** `LLM_GATEWAY_API_KEY` lives
in Azure Key Vault, not in `azd env get-values`' output — see
`docs/adr/0005-key-vault-for-gateway-secrets.md` for why. `azd up`
already granted your signed-in account read access to it, so fetch and
export it once per terminal, right after the block above:

```bash
export LLM_GATEWAY_API_KEY=$(az keyvault secret show \
  --vault-name "$AZURE_KEY_VAULT_NAME" \
  --name litellm-master-key \
  --query value -o tsv)
```

If a Python command fails with `RuntimeError: LLM_GATEWAY_API_KEY is not
set`, this is the command you forgot to re-run in that terminal — same
category of mistake as forgetting `source <(azd env get-values)` above,
just a second command instead of folded into the first.

### 0.8 — Verify the deployment end-to-end

```bash
pip install -r requirements.txt --break-system-packages --quiet
python scripts/verify_deployment.py --url "$SERVICE_API_ENDPOINT_URL"
```

Expected output: five `[PASS]` lines and `ALL CHECKS PASSED`. This script
sends real requests to your live Container App, which calls real Azure
OpenAI, real Azure AI Search, and real Azure AI Content Safety — a pass
here means the entire cloud stack is correctly wired, not just that
resources exist. See "Troubleshooting" at the end of this guide if
anything fails.

**Stop here.** Part 0 is complete. Everything below is the timed,
in-class, ~30-minute lab.

---

## Part 1 — What you're about to repair (5 min read, no commands yet)

### The scenario

Nimbus Telecom's support copilot handles four safe, read-only lookups
automatically (plan info, outage status, latest bill, and support-ticket
creation) and one sensitive action — proposing a monetary account credit
— which always requires human approval before anything moves. A teammate
just opened a pull request changing the agent's system prompt to make its
answers shorter. Automated evaluation blocked the release. Your job is to
find out why, and fix it, using nothing but the automated pipeline this
lab already has.

### The architecture, in the scope this lab touches

```
   trainee / CI job
        │
        ▼
  app/agent/agent.py            run_turn() — orchestrates every stage below
        │
        ├─▶ app/retrieval/retrieval.py     retrieve()          → Azure AI Search (real)
        ├─▶ app/agent/tool_policy.py       agent_policy_layer() → deterministic, in-process
        ├─▶ app/agent/content_safety.py    check_content()     → Azure AI Content Safety (real)
        ├─▶ app/agent/manager_agent.py     route()             → Foundry (real) — picks "billing" or "account"
        ├─▶ app/agent/billing_agent.py     decide()            → Foundry (real) — only if routed to billing
        │      (or app/agent/account_agent.py decide(), if routed to account)
        ├─▶ app/agent/tool_policy.py       enforce_tool_execution_boundary() → deterministic, in-process
        └─▶ app/agent/cost_tracking.py     estimate_turn_cost() → local estimate, no call
```

Two agents handle every turn, not one: **ManagerAgent** first decides
which specialist should handle the message (it never sees the real tool
schemas and cannot call a tool itself), then that specialist makes its
own real Foundry function-calling call, offered only its own subset of
the five tools (`app/agent/tools.py`, split in `app/agent/tool_schemas.py`)
— narrowed further by the same two independent guardrail layers as
before:

1. **`agent_policy_layer()`** (`app/agent/tool_policy.py`) decides which
   tools are even *offered* to the model this turn — a monetary-credit
   tool is only offered when the message contains a credit/refund/money
   trigger phrase, or withheld entirely if the input-layer classifier
   flags the message as a likely prompt injection.
2. **`enforce_tool_execution_boundary()`** (same file) is the
   deterministic gate every tool call passes through before it actually
   runs — argument validation, per-customer identity checks, the $50
   per-transaction credit ceiling, and the 3-per-day credit-request limit
   are all enforced here, in code, regardless of what the model decided.

Both layers are unchanged by, and apply identically regardless of, the
Manager/Specialist split — see `docs/adr/0001-foundry-and-multiagent.md`.
This lab's scenario is entirely a BillingAgent-side fault: the credit
tool only ever lives on BillingAgent (`request_customer_credit` isn't in
AccountAgent's tool set at all), so the disambiguation fix below only
ever needs to touch BillingAgent's prompt.

The system prompt itself lives as a versioned Markdown file, not a Python
string:

| File | `version` | What it is |
|---|---|---|
| `app/prompts/system_prompt_billing_baseline.md` | `baseline` | The known-good BillingAgent prompt currently in production. Includes an explicit disambiguation instruction: *"a general or ambiguous billing complaint should create a support ticket, not a credit request — only propose a credit when the customer explicitly asks for one."* |
| `app/prompts/system_prompt_billing_candidate_broken.md` | `candidate_broken` | The pull request under review. Shorter answers, as intended — but the disambiguation instruction was deleted in the edit. This is the injected fault for this lab. |
| `app/prompts/system_prompt_billing_candidate_fixed.md` | `candidate_fixed` | The corrected version: same short-answer style, disambiguation instruction restored. |

(`system_prompt_manager.md` and `system_prompt_account.md` are unrelated
to this lab's fault — the manager's routing logic and AccountAgent's
prompt are untouched by the candidate PR.)

### Why this is a realistic failure mode, not a contrived one

`agent_policy_layer()`'s trigger-phrase gate only controls whether the
credit tool is *offered*. Once it's offered, which tool the model
actually *picks* for an ambiguous message like "My bill seems wrong, can I get some money back?" is a genuine model judgment call, steered
entirely by the system prompt's instructions. Delete the disambiguation
sentence, and a well-behaved model reasonably starts treating "sort it
out" as an implicit credit request instead of routing to
`create_support_ticket` — exactly what `knowledge_docs/billing_dispute_policy.md`
says should happen for a bare dispute. Nothing crashes, nothing throws an
exception, no guardrail fires. The agent just quietly starts making the
wrong tool choice on a specific, narrow slice of ambiguous inputs — which
is precisely the failure mode layered evaluation, not a single test, is
designed to catch.

---

## Part 2 — Reproduce the failure (≈8 min)

### 2.1 — Try the baseline prompt first

```bash
python -m app.agent.agent "My bill seems wrong, can I get some money back?" --prompt-version baseline
```

Read the JSON output. `tool_call.name` should be `create_support_ticket`
— the correct behavior, matching `billing_dispute_policy.md`.

### 2.2 — Try the candidate prompt under review

```bash
python -m app.agent.agent "My bill seems wrong, can I get some money back?" --prompt-version candidate_broken
```

`tool_call.name` is now `request_customer_credit` on at least some runs
(Azure OpenAI's output isn't perfectly deterministic turn to turn — if you
get `create_support_ticket` once, try two or three times, or trust the
dataset-driven eval in the next step, which is what actually gates the
release). Note: this does **not** mean money moved. Look at
`tool_result.state` — it's `PENDING_APPROVAL`, because
`enforce_tool_execution_boundary()` never lets any credit request
auto-execute regardless of which tool the model picked. The bug is a
wrong *decision*, not a safety-boundary failure — an important
distinction you'll see the evaluation layers below treat very
differently.

### 2.3 — Run the real evaluation dataset against the candidate

Diffing two or three manual tries isn't how this actually gets caught —
`eval/datasets/tool_trajectory_eval.jsonl` has 8 cases with a known
expected tool for each, including several ambiguous-billing cases shaped
like the one above. Every one of these calls run against your real
deployed Azure OpenAI, Azure AI Search, and Azure AI Content Safety —
there is no mocked or offline mode.

```bash
python -m eval.run_agent_trajectory_eval \
  --dataset eval/datasets/tool_trajectory_eval.jsonl \
  --prompt-version candidate_broken \
  --out eval/results/agent_eval.json

cat eval/results/agent_eval.json | python -m json.tool
```

Look at `metrics.tool_selection_accuracy` — it should be below the
`release-policy.yaml` threshold of `0.90`. Look at
`metrics.unauthorized_action_rate` too — this should still read `0.0`,
because no credit was ever auto-approved; only the *selection* metric
should be breached. That distinction is the whole point of Day 1's
guardrail design: a wrong decision degrades a quality metric (`hold`), an
unauthorized *action* is zero-tolerance (`reject`) — see
`release-policy.yaml`'s `decision_matrix`.

### 2.4 — Run the full release gate

```bash
python -m eval.run_llm_eval \
  --dataset eval/datasets/rag_eval.jsonl \
  --prompt-version candidate_broken \
  --out eval/results/llm_eval.json

python -m eval.run_safety_regression \
  --dataset eval/datasets/safety_regression.jsonl \
  --dataset eval/datasets/prompt_injection.jsonl \
  --out eval/results/safety_eval.json

python -m eval.apply_release_policy \
  --policy release-policy.yaml \
  --results eval/results/llm_eval.json eval/results/agent_eval.json eval/results/safety_eval.json \
  --out eval/results/decision.json

python -m eval.post_pr_summary --decision eval/results/decision.json
```

Expected: `decision: HOLD`, with a breach entry naming
`tool_selection_accuracy`. This is the exact Markdown block that would be
posted as a PR comment in CI — see `.github/workflows/ai-release.yml`'s
`release-gate` job, which runs precisely these four commands.

---

## Part 3 — Find and understand the root cause (≈5 min)

```bash
diff app/prompts/system_prompt_billing_baseline.md app/prompts/system_prompt_billing_candidate_broken.md
```

You should see the disambiguation sentence present in `baseline` and
absent in `candidate_broken`. Open both files directly and read the whole
prompt body — notice the front matter at the top of each
(`prompt_id: nimbus-billing-agent`, `version: ...`) is what
`app.agent.prompt_loader.load_prompt()` parses and what
`tests/validate_prompt_templates.py` checks the shape of on every commit,
independent of whether the wording inside is good or bad.

Optionally, inspect the actual trace this turn produced —
`app/agent/agent.py` writes a full span-by-span record of every stage
(retrieval query, policy decision, content-safety result, manager
routing, specialist tool selection, tool execution, answer generation) to
`eval/traces/<trace_id>.json` on every real run:

```bash
ls -t eval/traces/*.json | head -1 | xargs cat | python -m json.tool
```

This is the same trace shape that gets exported to Application Insights
in production (see `app/api/main.py`'s `configure_azure_monitor()` call)
— you're looking at exactly what an on-call engineer would see in a real
incident, not a lab-only format.

---

## Part 4 — Apply and verify the fix (≈7 min)

### 4.1 — Confirm the fixed candidate resolves it

```bash
python -m eval.run_agent_trajectory_eval \
  --dataset eval/datasets/tool_trajectory_eval.jsonl \
  --prompt-version candidate_fixed \
  --out eval/results/agent_eval.json

cat eval/results/agent_eval.json | python -m json.tool
```

`tool_selection_accuracy` should now be `1.0` (or comfortably above the
`0.90` threshold).

### 4.2 — Re-run the full gate

```bash
python -m eval.run_llm_eval \
  --dataset eval/datasets/rag_eval.jsonl \
  --prompt-version candidate_fixed \
  --out eval/results/llm_eval.json

python -m eval.run_safety_regression \
  --dataset eval/datasets/safety_regression.jsonl \
  --dataset eval/datasets/prompt_injection.jsonl \
  --out eval/results/safety_eval.json

python -m eval.apply_release_policy \
  --policy release-policy.yaml \
  --results eval/results/llm_eval.json eval/results/agent_eval.json eval/results/safety_eval.json \
  --out eval/results/decision.json

python -m eval.gate_or_fail --decision eval/results/decision.json
```

Expected: `Release gate PASSED: decision = PROMOTE`, exit code `0`.

### 4.3 — See it enforced in CI, not just locally

Push a branch that sets `PROMPT_VERSION: candidate_broken` in
`.github/workflows/ai-release.yml`'s `env:` block and open a PR — the
`deterministic-tests`, `code-scanning`, `cloud-eval`, and `release-gate`
jobs all run on every PR. `code-scanning` is a clearly-labeled
**placeholder** stage (`scripts/mock_security_scan.py`) — it always
reports a mock PASS and exists to show where a real SAST/secrets/CVE
scanner (Semgrep/Gitleaks/Trivy/Checkov, or SonarQube) would plug in,
not to actually catch anything; it doesn't affect this lab's outcome
either way. `cloud-eval` and `release-gate` will reproduce the exact
`HOLD` you just saw locally, against a shared cloud evaluation
environment (see "CI/CD and cloud credentials" in `README.md` for how
that shared environment is configured once, by an instructor/admin,
ahead of the class).

Revert the change to `PROMPT_VERSION: baseline` (or point it at
`candidate_fixed`) and push again — the same jobs now pass, and on a
merge to `main`, three deploy jobs run in sequence:
`deploy-dev` (automatic) → `deploy-staging` (automatic, only after dev's
`verify_deployment.py` passes) → `deploy-production` (gated behind the
`production` GitHub Environment's required-reviewer approval). All three
promote the exact same built image (this commit's SHA) through
`nimbus-dev` → `nimbus-staging` → `nimbus-production` — see
`docs/adr/0001-foundry-and-multiagent.md` for why each environment is
sized differently (Search tier, Foundry capacity, Container App min
replicas) even though the code and image are identical across all three.

---

## Part 5 — Deliverables and discussion

**Produce, and be ready to show:**
1. The `eval/results/decision.json` from the broken candidate (`HOLD`,
   with the `tool_selection_accuracy` breach) and from the fixed one
   (`PROMOTE`).
2. A one-sentence explanation of why `unauthorized_action_rate` stayed
   at `0.0` throughout, even while the release was correctly blocked.
3. The one-line prompt diff that fixed it.

**Discussion questions:**
- Why does this project score tool *selection* and monetary *execution*
  as two entirely separate metrics with two entirely different
  `on_breach` severities (`hold` vs. `reject`)? What real-world failure
  would each one alone fail to catch?
- `agent_policy_layer()`'s trigger-phrase gate and the system prompt's
  disambiguation sentence are two different mechanisms nudging the same
  outcome. Which one actually *prevented* an unauthorized credit from
  executing here, and which one only ever influenced which tool got
  *offered* or *picked*? (Answer: neither did — the tool executed exactly
  as designed, into `PENDING_APPROVAL`; the fix here is a *quality*
  fix, not a *safety* fix. The Day 2 guardrail mini-lab is where you'll
  exercise the layer that actually blocks something outright.)
- This lab used real Azure OpenAI calls, which means results can vary
  slightly run to run. What does `eval/datasets/tool_trajectory_eval.jsonl`
  having 8 cases, not 1, buy you that a single manual test in Part 2.2
  didn't?

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `RuntimeError: LLM_GATEWAY_ENDPOINT is not set` | You opened a new terminal and didn't reload the environment | Re-run Part 0.7's `source <(azd env get-values)` in this terminal |
| `RuntimeError: LLM_GATEWAY_API_KEY is not set` | Same as above, but for the one value Key Vault holds instead of `.env` | Re-run Part 0.7's `az keyvault secret show ...` export in this terminal |
| `azd up` fails at the OpenAI resource with a quota/capacity error | No `gpt-4o-mini` capacity in your chosen region | Pick a different region: `azd env set AZURE_LOCATION <region>` then `azd up` again — it's idempotent, already-created resources are left alone |
| `azd up` fails with a role-assignment permission error | Your account has Contributor but not User Access Administrator | Ask your Azure admin to grant it, or have them run `azd provision` once on your behalf |
| `azd deploy` fails to build the Docker image | Docker daemon not running locally | Start Docker Desktop / `sudo systemctl start docker`, or switch `docker.remoteBuild: true` under `services.api` in `azure.yaml` to build in ACR instead of locally |
| `python scripts/verify_deployment.py` fails the RAG-grounding check | The Search index wasn't seeded | Re-run the postprovision hook manually: `python -m scripts.build_search_index` |
| `python scripts/verify_deployment.py` fails the guardrail check | Content Safety endpoint not reachable, or `content_safety.py` raised before returning | Check `az containerapp logs show` for the actual exception; confirm `AZURE_CONTENT_SAFETY_ENDPOINT` is set on the Container App with `azd env get-values \| grep CONTENT_SAFETY` |
| Azure OpenAI calls are slow / rate-limited during a full class running eval scripts simultaneously | Shared quota across trainees deploying to the same subscription | Each trainee should provision their own `azd env` (their own Azure OpenAI deployment) rather than sharing one — see Part 0.5 |

There is no local/offline fallback path anywhere in this lab. If a
command fails because of a cloud service, the fix is always to fix the
cloud configuration (region, quota, RBAC, endpoint), never to fall back
to a stub — there isn't one.

---

## What's next

Day 2 picks up exactly where this environment is left — no re-provisioning,
no `azd down`. Leave `azd env get-values` loaded in your shell (or re-run
Part 0.7 if you start a fresh terminal) and go to `Day2_Lab_Guide.md`.
