# Day 1 Demo Playbook — Everything, In the Order You'll Actually Do It

This is a run-through document, not a teaching document — `Day1_Lab_Guide.md`
explains *why* each piece exists; this just tells you *when* to do each
thing and gives you the exact command to copy. Organized around the three
questions that actually matter for planning a demo:

1. **What can I show with nothing but my own laptop and Azure subscription?**
   (Phase 1 — no GitHub involved at all)
2. **When do I need a separate "eval" environment, and why can't GitHub just
   use my local one?** (Phase 3)
3. **When does GitHub Actions create its own infrastructure, vs. when does
   it just point at something I already created?** (Phase 5 answers this
   directly — it's the single most confusing part of this whole setup.)

Every phase says up front whether you actually need it for the demo you're
planning, so you can stop after Phase 1 if that's all you want to show.

---

## Phase 0 — Before you touch anything (one time, ~5 min)

Confirm these are true. If any aren't, fix them before starting — everything
downstream assumes them:

- `az`, `azd` (1.9+), `gh`, `python3.12`, `docker`, `git` all installed and
  on your PATH.
- You're an **admin** on the GitHub repo you'll use (fork or your own copy)
  — Phases 2 and 4 need to write repo secrets/variables and create
  Environments.
- Your Azure subscription has **Owner**, or **Contributor + User Access
  Administrator** — the deployment creates role assignments, plain
  Contributor isn't enough.
- `gpt-5-mini` capacity is approved somewhere on that subscription. Check:
  ```bash
  az cognitiveservices account list-skus --kind OpenAI --location eastus --query "[?name=='S0']" -o table
  ```
  (swap `eastus` for whatever region you plan to deploy into — try 2–3
  regions if the first comes back empty)

---

## Phase 1 — Run it locally (no GitHub, no eval environment, nothing shared)

**When you need this:** Always — this is the foundation everything else
sits on, and it's also a complete demo on its own if you don't need the
CI/LiteLLM/Langfuse story.

**What this creates:** Your own private sandbox environment (`nimbus-demo`)
— Foundry, Search, Content Safety, both Container Apps, the LiteLLM
gateway. Nothing here is shared with anyone, nothing here is visible to
GitHub.

```bash
git clone <repo-url> nimbus-support-copilot
cd nimbus-support-copilot
./scripts/seed_lab.sh                    # free, offline — confirm "All local checks passed"
az login
azd auth login
azd env new nimbus-demo
azd up                                   # ~15-20 min, mostly unattended — pick your subscription + a region with real gpt-5-mini capacity
```

**If `azd up` fails on the Foundry *project* step** with `BadRequest:
Unsupported configuration. To create projects, you must enable a managed
identity on your resource` — this is a real, known Azure timing race (the
account's identity needs a moment to propagate through Azure's own
backend before project creation will accept it), not something you did
wrong. Fix:

```bash
azd env set DEPLOY_FOUNDRY_PROJECT false
azd provision                            # creates the account + its identity, skips the project
# wait a few minutes — don't chain the next command immediately
azd env set DEPLOY_FOUNDRY_PROJECT true
azd provision                            # separate deployment — creates just the project
azd env unset DEPLOY_FOUNDRY_PROJECT     # back to normal for next time
```

**If it instead fails with `A resource with this name already exists or is
in a conflicting state`** and mentions soft-delete — a leftover from an
earlier failed attempt is blocking the name:

```bash
az cognitiveservices account purge --name <account-name> --resource-group <rg-name> --location <region>
azd up
```

Once `azd up` finishes, load its values into every terminal you use from
here on:

```bash
set -a
source <(azd env get-values)
set +a
export LLM_GATEWAY_API_KEY=$(az keyvault secret show \
  --vault-name "$AZURE_KEY_VAULT_NAME" \
  --name litellm-master-key \
  --query value -o tsv)
```

Confirm it's actually live:

```bash
pip install -r requirements.txt --break-system-packages --quiet
python scripts/verify_deployment.py --url "$SERVICE_API_ENDPOINT_URL"
```
Expect five `[PASS]` lines and `ALL CHECKS PASSED`.

### The demo beat itself — the bug, live

```bash
# 1. Correct behavior
python -m app.agent.agent "My bill seems wrong, can I get some money back?" --prompt-version baseline
# tool_call.name should be create_support_ticket

# 2. The regression
python -m app.agent.agent "My bill seems wrong, can I get some money back?" --prompt-version candidate_broken
# tool_call.name may now be request_customer_credit -- either way, tool_result.state is PENDING_APPROVAL, nothing auto-executes

# 3. The automated gate catching it
python -m eval.run_agent_trajectory_eval --dataset eval/datasets/tool_trajectory_eval.jsonl --prompt-version candidate_broken --out eval/results/agent_eval.json
cat eval/results/agent_eval.json | python -m json.tool
# tool_selection_accuracy below the 0.90 threshold

# 4. The root cause
diff app/prompts/system_prompt_billing_baseline.md app/prompts/system_prompt_billing_candidate_broken.md
# one missing sentence

# 5. The fix, confirmed
python -m eval.run_agent_trajectory_eval --dataset eval/datasets/tool_trajectory_eval.jsonl --prompt-version candidate_fixed --out eval/results/agent_eval.json
cat eval/results/agent_eval.json | python -m json.tool
# tool_selection_accuracy back to 1.0
```

**Stop here if that's the whole demo you need.** Everything below is only
for showing the same story enforced automatically inside GitHub Actions,
plus real LiteLLM/Langfuse traces.

---

## Phase 2 — Give GitHub its own Azure credentials (deploy identity)

**When you need this:** Only if GitHub Actions will deploy anything
(`deploy-dev`/`deploy-staging`/`deploy-production`) or you want
`cloud-eval` to exist at all — both need *some* identity for GitHub to
authenticate to Azure as. Do this once per repo, not per demo.

**What this does NOT do:** create any environment or endpoints. It only
gives GitHub a passwordless login (OIDC) it can use later, when a job
actually runs.

```bash
azd env new nimbus-ci              # throwaway -- just gives azd pipeline config a subscription/location to read
azd pipeline config
```

If it fails partway writing secrets, it's almost always `gh` auth scope:
```bash
gh auth login --hostname github.com --git-protocol https --scopes repo,workflow
azd pipeline config                # safe to re-run
```

This writes exactly four things to your repo — Settings → Secrets and
variables → Actions:

| Name | Kind |
|---|---|
| `AZURE_CLIENT_ID` / `AZURE_TENANT_ID` / `AZURE_SUBSCRIPTION_ID` | secret |
| `AZURE_LOCATION` | variable |

That's it. **No endpoints, no environment-specific values** — just enough
for `deploy-dev`/`deploy-staging`/`deploy-production` to log in and
provision on their own later (see Phase 5 for exactly how).

---

## Phase 3 — The shared eval environment (only if you want `cloud-eval` + real LiteLLM/Langfuse in the demo)

**When you need this:** Only if you're showing `cloud-eval` running inside
a PR. Skip this whole phase if your demo stops at Phase 1.

**Why this can't just reuse `nimbus-demo` from Phase 1:** `cloud-eval` runs
on every PR against a shared environment, deliberately separate from
anything serving real deployed traffic — a PR under review should never be
able to call (or corrupt) an environment someone's actually using.

**Set Langfuse keys BEFORE `azd up`, not after** — the gateway reads them
at deploy time, so setting them later means re-provisioning anyway. A free
project at https://cloud.langfuse.com takes under a minute to create.
Skip these two lines entirely if you don't want to demo Langfuse.

```bash
azd env new nimbus-eval
azd env set LANGFUSE_PUBLIC_KEY pk-lf-...
azd env set LANGFUSE_SECRET_KEY sk-lf-...
azd up
```

Same two failure modes as Phase 1 can happen here too (identity
propagation race, soft-delete conflict) — same fixes apply, just against
`rg-nimbus-eval` instead.

**Create the identity `cloud-eval` will authenticate as** — a *separate*
one from Phase 2's, because it needs a federated credential scoped to
`pull_request` runs, which Phase 2's `main`-branch-scoped one can't
satisfy:

```bash
APP_ID=$(az ad app create --display-name nimbus-eval-cloudeval --query appId -o tsv)
az ad sp create --id "$APP_ID"

az ad app federated-credential create --id "$APP_ID" --parameters '{
  "name": "github-pull-request",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:<org>/<repo>:pull_request",
  "audiences": ["api://AzureADTokenExchange"]
}'

SP_OBJECT_ID=$(az ad sp show --id "$APP_ID" --query id -o tsv)
SCOPE="/subscriptions/$(az account show --query id -o tsv)/resourceGroups/rg-nimbus-eval"
az role assignment create --assignee "$SP_OBJECT_ID" --role "Cognitive Services User" --scope "$SCOPE"
az role assignment create --assignee "$SP_OBJECT_ID" --role "Search Index Data Reader" --scope "$SCOPE"
az role assignment create --assignee "$SP_OBJECT_ID" --role "Key Vault Secrets User" --scope "$SCOPE"
```

Pull the values GitHub needs:

```bash
azd env select nimbus-eval
azd env get-values
```
Note down `AZURE_OPENAI_DEPLOYMENT`, `AZURE_SEARCH_ENDPOINT`,
`AZURE_CONTENT_SAFETY_ENDPOINT`, `LLM_GATEWAY_ENDPOINT`,
`AZURE_KEY_VAULT_NAME`.

---

## Phase 4 — Wire it all into GitHub

**When you need this:** Same condition as Phase 3 — skip if you stopped
after Phase 1.

Settings → Secrets and variables → Actions:

| Add | Kind | Value |
|---|---|---|
| `EVAL_AZURE_CLIENT_ID` | secret | `$APP_ID` from Phase 3 |
| `EVAL_AZURE_TENANT_ID` | secret | `az account show --query tenantId -o tsv` |
| `EVAL_AZURE_SUBSCRIPTION_ID` | secret | `az account show --query id -o tsv` |
| `EVAL_AZURE_OPENAI_DEPLOYMENT` | variable | from Phase 3's `azd env get-values` |
| `EVAL_AZURE_SEARCH_ENDPOINT` | variable | same |
| `EVAL_AZURE_CONTENT_SAFETY_ENDPOINT` | variable | same |
| `EVAL_LLM_GATEWAY_ENDPOINT` | variable | same |
| `EVAL_AZURE_KEY_VAULT_NAME` | variable | same |

Settings → Environments — create `development`, `staging`, `production`
(exact names, case-sensitive), **required reviewer on all three** — this
is what stops a merge from silently spending money; only needed if your
demo shows the merge → approve → deploy part.

**Last step, and the only one that turns spending on:** add variable
`ENABLE_CLOUD_EVAL` = `true`. Everything above this line can sit in the
repo costing nothing per PR — this one flag is what actually starts real
Azure calls happening on every PR.

---

## Phase 5 — When does GitHub Actions actually create infrastructure?

This is the part worth understanding clearly before you demo it, because
the answer is different for the two kinds of jobs in `ai-release.yml`:

**`cloud-eval` never creates anything.** It's a Python process on a
GitHub-hosted runner making direct calls to Search / Content Safety / the
LiteLLM gateway, using the literal endpoint URLs you handed it in Phase 4.
If `nimbus-eval` isn't already up and running (Phase 3), `cloud-eval` has
nothing to call — it doesn't provision it for you.

**`deploy-dev` / `deploy-staging` / `deploy-production` create their own
infrastructure, automatically, the first time each one runs — you never
provision `nimbus-dev` yourself.** Each job runs:
```bash
azd env select nimbus-dev || azd env new nimbus-dev --no-prompt --location "$AZURE_LOCATION" --subscription "$AZURE_SUBSCRIPTION_ID"
azd provision --no-prompt
azd deploy --no-prompt
```
First run: nothing to select, so it creates the environment from scratch
using only Phase 2's four values (credentials + region) and builds every
resource. It's safe to run again on every merge after that — CI runners
are ephemeral, so there's no persisted local state, but `resourceToken` is
a deterministic hash of subscription + environment name + location, so
`nimbus-dev` always resolves to the same resource group, and `azd
provision` only updates what actually drifted.

**So:** you create `nimbus-eval` yourself, once, by hand (Phase 3).
`nimbus-dev`/`staging`/`production` create themselves, the moment you
approve their first deployment — nothing to pre-provision for those
three.

---

## Phase 6 — Run the actual demo, end to end

1. Create a branch, set `PROMPT_VERSION: candidate_broken` in
   `.github/workflows/ai-release.yml`'s `env:` block, push, open a PR.
2. Watch `deterministic-tests` and `code-scanning` pass (free, instant).
3. Watch `cloud-eval` run real calls — if you set Langfuse keys in Phase
   3, have your Langfuse Cloud project open in another tab; traces land
   within seconds of each step.
4. Watch `release-gate` report **HOLD**, posted as a PR comment — the
   same result you saw locally in Phase 1, now enforced in CI.
5. Change `PROMPT_VERSION` to `baseline` (or `candidate_fixed`), push to
   the same PR. Watch all four checks pass, **PROMOTE**.
6. Merge the PR. `deploy-dev` appears in the PR's **Deployments** view,
   **Waiting**. Click **Review deployments → Approve** — narrate that
   nothing happens until a human says yes. Watch it provision `nimbus-dev`
   (Phase 5's automatic creation, happening live) and deploy. Approve
   `deploy-staging`, then `deploy-production`, the same way, if you want
   the full three-environment promotion.

---

## Cleanup (right after the demo, not later)

```bash
azd down --purge        # once per environment you created: nimbus-demo, nimbus-eval, nimbus-dev/staging/production if approved
```

In GitHub: set `ENABLE_CLOUD_EVAL` back off (or unset) — leaving it `true`
means every future PR against this repo keeps making real, billed Azure
calls.
