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

Part 0 has two tracks with two different owners, and they run in
**parallel**, not in sequence — nobody should be waiting on anybody else:

| Track | Who | Steps | When |
|---|---|---|---|
| **A — Your own sandbox** | Every trainee, individually | 0.1 → 0.8 below | Evening before, or first thing before class |
| **B — Shared CI/CD** | Instructor/admin, once for the whole class, **and entirely optional** | 0.9 below | Any time before Part 4.3 runs in class — does **not** block Track A and doesn't need to happen first or last relative to it |

If you're a trainee: do 0.1–0.8 below, stop at the line that says **"Stop
here,"** and go read Part 1. You do not need 0.9 and should not run it —
running `azd pipeline config` against the shared class repo more than
once from different accounts is exactly the kind of thing one admin
should own, not the whole room.

**Track B costs real money to turn on, and Day 1 does not require it.**
Every deliverable Part 5 asks for — both `decision.json` files, the
prompt diff, the one-sentence explanation — comes out of Track A alone,
run entirely on your own machine. Track B's only payoff is Part 4.3,
watching the same HOLD/PROMOTE decision happen automatically inside
GitHub instead of in your terminal — a demonstration, not new content.
The part of Track B that actually costs money, `cloud-eval`, is
off by default in `.github/workflows/ai-release.yml` (gated behind a
repo variable, `ENABLE_CLOUD_EVAL`, that starts unset) for exactly this
reason — see 0.9's checklist and the "should we even turn this on"
discussion right before it. If your class is optimizing for lowest
cost, the admin can skip 0.9 entirely, or do 0.9.a/0.9.c (deploy
credentials, GitHub Environments) without ever doing 0.9.b or flipping
`ENABLE_CLOUD_EVAL` to `true` — everything simply shows as a skipped,
not failed, check, and nobody is blocked from finishing the lab.

If you're the instructor/admin: do 0.9 once, whenever is convenient
before class starts — before, during, or after trainees work through
their own 0.1–0.8, it makes no difference, since 0.9 wires up the
*shared* repo's CI/CD and a *separate* shared evaluation environment,
neither of which any trainee's individual `nimbus-lab[-initials]`
sandbox from Track A depends on. The only hard deadline is: it must be
done before anyone in the room reaches Part 4.3 ("See it enforced in
CI"), since that's the first point in the lab that actually exercises
the pipeline 0.9 sets up.

**Why Track B doesn't make Track A unnecessary, even though both end in
`azd provision`/`azd deploy`:** it's tempting to look at 0.9 and think
"CI creates infrastructure too, so why am I also creating infrastructure
by hand?" The two builds are the same *commands* pointed at two
different environments doing two different jobs, and one of them can't
exist yet at the point you'd need it:

| | Track A — `nimbus-lab[-initials]` | Track B — `nimbus-dev`/`staging`/`production` |
|---|---|---|
| Created by | You, running `azd up` yourself (0.6) | GitHub Actions, inside the `deploy-*` jobs, only after a merge to `main` |
| Exists | Before you know what the fix is | Only after a fix has already been merged |
| What it's for | Somewhere real for the `python -m app.agent.agent ...` and `python -m eval.run_*` commands in Parts 2–5 to actually call | Demonstrating an already-approved change getting promoted through a real pipeline (Part 4.3, optional) |
| You interact with it | Directly, from your own terminal, via `source <(azd env get-values)` | Not really — it's the shared repo's CI-managed deployment |

Parts 2–5 are you *finding* the fix — diagnosing why `candidate_broken`
fails, confirming `candidate_fixed` resolves it. None of that can happen
against `nimbus-dev`/`staging`/`production`, because those three don't
get created until *after* a merge to `main` — and you can't merge a fix
to `main` before you've found it. So Track B is never a substitute for
Track A; it's downstream of it. Skipping your own `azd up` doesn't mean
"let CI do it instead" — it means you'd have nothing to run the entire
diagnosis phase of the lab against.

Do Track A the evening before, or first thing before the training
session begins. It is entirely automated — one command does the
provisioning, building, and deploying — but Azure resource creation and
RBAC propagation genuinely take 15–20 minutes of wall-clock time that
would eat most of an in-class lab if you started it live. Everything in
"Part 1" onward assumes both tracks are already done and takes the ~30
minutes it promises.

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
resource creation) and available `gpt-5-mini` capacity for your target
Foundry account's region. If you've never deployed Azure OpenAI on this subscription
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
- **Azure location** — pick a region with `gpt-5-mini` capacity.
  **The region list below was verified for the now-retired `gpt-4o-mini`
  and has not been re-verified for `gpt-5-mini` or its GlobalStandard SKU
  (which routes inference globally but still needs the Foundry account
  itself provisioned somewhere) — treat it as a starting point, not a
  guarantee, and confirm in the Foundry portal or with the command below
  before relying on it.** As of the old check, `eastus2`,
  `swedencentral`, and `westus3` reliably had `gpt-4o-mini` capacity;
  verify current availability for your subscription with:
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
   Foundry account + project + `gpt-5-mini` deployment (one shared
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
pip install --user -r requirements.txt --break-system-packages --quiet
python scripts/verify_deployment.py --url "$SERVICE_API_ENDPOINT_URL"
```

Expected output: five `[PASS]` lines and `ALL CHECKS PASSED`. This script
sends real requests to your live Container App, which calls real Azure
OpenAI, real Azure AI Search, and real Azure AI Content Safety — a pass
here means the entire cloud stack is correctly wired, not just that
resources exist. See "Troubleshooting" at the end of this guide if
anything fails.

**Stop here if you're a trainee.** Track A (your own sandbox) is
complete. Everything below in 0.9 is the admin-only Track B — skip
straight to Part 1, the timed, in-class, ~30-minute lab.

### 0.9 — Wire up CI/CD (instructor/admin, once for the whole class — trainees, skip to Part 1)

This section has four parts, in this order: (a) run `azd pipeline
config` for the deploy side — or its alternative, 0.9.a′, if you'd
rather use a user-assigned managed identity than the app registration
`azd` creates by default — (b) stand up a separate shared evaluation
environment and wire its credentials in by hand, (c) create the GitHub
Environments that gate production, (d) prove the whole thing actually
works before anyone in the room depends on it. Do them in that order —
(b) and (c) both add to the same repo settings screen (a) or (a′) also
writes to, and it's easier to see what's yours versus what was
automated if that goes first; (d) has to come last
because it exercises everything (a)–(c) set up.

**First, a decision, not a checklist item: do you even want `cloud-eval`
turned on?** It's the one job in this whole file with a real, unavoidable
per-run cost — real Azure OpenAI/Search/Content Safety calls — and it's
**off by default**, gated behind a repo variable called
`ENABLE_CLOUD_EVAL` that `ai-release.yml`'s header comment documents in
full. Leave it unset and rows 4–5 below don't apply to you at all: skip
0.9.b entirely, skip straight to row 6. Nothing in Part 5's required
deliverables needs `cloud-eval` to  have ever run — see the note at the
top of this Part 0 section. Only work through rows 4–5 and flip the
variable on if you specifically want Part 4.3's live demonstration.

**Checklist — every one of these must be true before Part 4.3 will
work (rows 4–5 only if you decided above to turn `cloud-eval` on).**
Miss any single row and the failure surfaces inside a GitHub Actions log
during class, which is a much worse place to debug it than here. Each
row names which sub-step sets it and how to confirm it independently:

| # | Requirement | Set in | Confirm with |
|---|---|---|---|
| 1 | `gh` CLI installed and authenticated with `repo` + `workflow` scope | 0.9.a | `gh auth status` |
| 2 | Deploy identity (app registration **or** user-assigned managed identity — pick one) + OIDC federated credential exists, subject scoped to this repo's `main` branch | 0.9.a (`azd pipeline config`) **or** 0.9.a′ (UAMI) | GitHub → **Settings → Secrets and variables → Actions** shows `AZURE_CLIENT_ID`/`AZURE_TENANT_ID`/`AZURE_SUBSCRIPTION_ID` (Secrets tab) and `AZURE_LOCATION` (Variables tab) |
| 3 | `AZURE_LOCATION` is a region with real `gpt-5-mini` capacity | 0.9.a | `az cognitiveservices account list-skus --kind OpenAI --location <region> --query "[?name=='S0']" -o table` — same check as Part 0.6 |
| 4 *(optional — only if `cloud-eval` is on)* | Shared `nimbus-eval` environment is provisioned and live | 0.9.b (`azd up`) | `azd env select nimbus-eval && azd env get-values` returns real, non-empty endpoint values |
| 5 *(optional — only if `cloud-eval` is on)* | A **separate** eval app registration + OIDC federated credential exists, subject `repo:<org>/<repo>:pull_request` — not the same credential as row 2 — **and** the repo variable `ENABLE_CLOUD_EVAL` is set to `true` | 0.9.b | GitHub → **Settings → Secrets and variables → Actions** shows the three `EVAL_AZURE_*` secrets, the five `EVAL_*` variables, and `ENABLE_CLOUD_EVAL = true` |
| 6 | GitHub Environments `development`, `staging`, `production` exist, named exactly (case-sensitive, must match the workflow file's `environment:` keys) | 0.9.c | GitHub → **Settings → Environments** |
| 7 | A required reviewer is added on **all three** environments, not just production | 0.9.c | Same screen, each environment's "Deployment protection rules" |
| 8 | GitHub Actions is enabled on the repo at all | repo default, check once | GitHub → **Settings → Actions → General** → "Allow all actions and reusable workflows" |
| 9 | The full pipeline has actually run once, end to end, successfully | 0.9.d | see below — this is the only row that isn't "did I configure X," it's "does it actually work" |

If you left `ENABLE_CLOUD_EVAL` unset (rows 4–5 skipped): `cloud-eval`
will show as **skipped**, not failed, on every PR — and because
`release-gate` needs `cloud-eval` and every `deploy-*` job needs
`release-gate`, those all cascade to skipped too, automatically, with no
extra configuration required. That's GitHub Actions' documented default
behavior (a job whose `needs` was skipped is itself skipped, not
failed), not a workaround this guide is relying on informally. Rows 1–3
and 6–8 still apply either way — the deploy side of CI/CD is independent
of whether the paid evaluation side is switched on.

#### 0.9.a — Deploy credentials, via `azd`

`azd pipeline config` needs a local azd **environment** to read
subscription/location/name from — it does not need `azd up` to have
actually finished, or even to have been run at all. If you already have
one selected (for instance because you, the admin, also did Track A's
0.1–0.8 for your own sandbox), you can run the command as-is from that
same checkout and it'll use whichever environment `azd env list` shows
as current. If not, create a throwaway one first — it doesn't provision
anything by itself:

```bash
azd env new nimbus-pipeline-admin
azd pipeline config
```

This creates an Azure identity (an app registration in most `azd`
versions; some versions instead create a user-assigned managed identity,
particularly if it detects your account can't create app registrations
— check with `az identity list -o table` vs. `az ad app list
--display-name nimbus-pipeline-admin -o table` if you want to know which
one you got), an OIDC federated credential on it, and the repo
secrets/variables the three `deploy-*` jobs read
(`AZURE_CLIENT_ID`/`AZURE_TENANT_ID`/`AZURE_SUBSCRIPTION_ID`/`AZURE_LOCATION`)
— all automatically, no values to copy by hand. Either way, if this
command already succeeded for you, you're done here — skip 0.9.a′ below,
it's only for when this command's app-registration creation fails
outright. It does **not** provision
or deploy `nimbus-dev`/`nimbus-staging`/`nimbus-production` themselves —
look at `.github/workflows/ai-release.yml`'s `deploy-*` jobs and you'll
see each one runs its own `azd env new`/`azd env select` +
`azd provision` + `azd deploy` inside CI, the first time that job runs.
So there is nothing to `azd up` locally for those three environments at
all; `nimbus-pipeline-admin` above (or your own Track A environment, if
you reused it) is disposable once this command succeeds — its only job
was giving `azd pipeline config` a subscription and location to point
the new app registration at.

Under the hood this shells out to the GitHub CLI (`gh`) to write those
repo secrets/variables, so it needs `gh` installed and authenticated
against the repo you're configuring, with enough scope to manage Actions
secrets. If it fails partway with something like `gh: To use GitHub CLI
in this environment, run gh auth login` or a 403/404 while it's writing
secrets, that's this, not an Azure problem:

```bash
gh auth status                                   # confirms whether gh even sees a login
gh auth login --hostname github.com --git-protocol https --scopes repo,workflow
# already logged in but missing scope? refresh instead of re-logging-in:
gh auth refresh -h github.com -s repo,workflow
```

Then re-run `azd pipeline config` — it's safe to run again; it updates
the app registration and secrets/variables it already created rather
than duplicating them.

**A federated credential is scoped to a specific GitHub Actions trigger
context, not to the repo in general** — this matters for 0.9.b below, so
it's worth understanding here first. Every OIDC token GitHub Actions
mints carries a `sub` (subject) claim describing exactly what produced
it — for example `repo:<org>/<repo>:ref:refs/heads/main` for a push to
`main`, or `repo:<org>/<repo>:pull_request` for any pull-request-triggered
run, or `repo:<org>/<repo>:environment:production` for a job that
targets the `production` GitHub Environment. Azure only accepts the
token if some federated credential on the app registration has a
`subject` field matching that claim *exactly* — no wildcards, no partial
match. `azd pipeline config` creates a federated credential whose subject
matches this repo's `main` branch, because the three `deploy-*` jobs run
`on: push: branches: [main]`. That credential does **not** match a
pull-request run's `sub` claim, which is exactly why `cloud-eval` (which
runs `on: pull_request`) needs its own app registration with its own,
differently-scoped federated credential — see 0.9.b. A token/credential
subject mismatch surfaces as an Azure login failure in the job logs
(look for `AADSTS70021` or similar), not as a GitHub-side error, so if a
job's cloud login step fails, check which trigger produced the run
against which federated credential subject exists before assuming the
credential value itself is wrong.

##### 0.9.a′ — Alternative: a user-assigned managed identity instead of `azd`'s app registration

`azd pipeline config` above always creates an **App Registration**
(Service Principal) with the federated credential on it — that's not a
security choice, it's just the only identity type its `--principal-id`/
`--principal-name`/`--principal-role` flags know how to target. A
**user-assigned managed identity (UAMI)** with its own federated
credential is an equally valid, equally passwordless alternative — same
OIDC trust mechanism, same "no secret ever stored in GitHub," just a
different kind of Azure object holding the credential. Use this path
instead of 0.9.a if either applies to you: your account can create
resources (Contributor/Owner on a resource group) but can't create App
Registrations in Entra ID (a separate, directory-level permission many
orgs restrict more tightly), or you'd simply rather keep the CI identity
as an ordinary Azure resource you can see and delete like anything else
in a resource group, instead of a tenant-level Entra object. If you go
this route, do it **instead of** running `azd pipeline config` in
0.9.a, not in addition to it — pick one identity for the `deploy-*` jobs,
not two.

**Put this UAMI in its own small resource group that nothing else in
this project touches** — e.g. `rg-nimbus-ci`, created just for it. This
matters more than it looks: a UAMI is a resource that lives and dies
with its resource group, unlike an App Registration, which is a
tenant-level object independent of any resource group. If this identity
lived inside, say, `rg-nimbus-dev`, then tearing that environment down
(`azd down --purge` on `nimbus-dev`, or any cleanup pass) would silently
delete the CI pipeline's own credential along with it, and the next
merge to `main` would fail with no obvious connection to whatever got
cleaned up. A dedicated, untouched resource group avoids that entirely.

```bash
# 1. A resource group dedicated to the CI identity, nothing else
az group create --name rg-nimbus-ci --location <region>

# 2. The identity itself
az identity create \
  --name nimbus-ci-deploy \
  --resource-group rg-nimbus-ci \
  --location <region>

# 3. Federated credential trusting this repo's main branch — same subject
#    azd pipeline config would have used in 0.9.a
az identity federated-credential create \
  --name github-main \
  --identity-name nimbus-ci-deploy \
  --resource-group rg-nimbus-ci \
  --issuer 'https://token.actions.githubusercontent.com' \
  --subject 'repo:<org>/<repo>:ref:refs/heads/main' \
  --audiences 'api://AzureADTokenExchange'

# 4. Grant it the same roles azd pipeline config grants its app registration
#    by default (Contributor to provision/deploy, User Access Administrator
#    because infra/main.bicep creates role assignments of its own)
UAMI_PRINCIPAL_ID=$(az identity show --name nimbus-ci-deploy --resource-group rg-nimbus-ci --query principalId -o tsv)
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
az role assignment create --assignee "$UAMI_PRINCIPAL_ID" --role Contributor --scope "/subscriptions/$SUBSCRIPTION_ID"
az role assignment create --assignee "$UAMI_PRINCIPAL_ID" --role "User Access Administrator" --scope "/subscriptions/$SUBSCRIPTION_ID"

# 5. The value the deploy-* jobs need as AZURE_CLIENT_ID is the identity's
#    CLIENT id, not its principal/object id — they are different values
az identity show --name nimbus-ci-deploy --resource-group rg-nimbus-ci --query clientId -o tsv
```

Then set the same four values 0.9.a's table describes — `AZURE_CLIENT_ID`
(the `clientId` from step 5, not `principalId`), `AZURE_TENANT_ID`,
`AZURE_SUBSCRIPTION_ID` as **secrets**, and `AZURE_LOCATION` as a
**variable** — by hand in GitHub (**Settings → Secrets and variables →
Actions**), since there's no `azd pipeline config` step doing it for
you on this path. `azd auth login --client-id ... --federated-credential-provider
"github" --tenant-id ...` (what every `deploy-*` job already runs, per
`ai-release.yml`) accepts a UAMI's client ID exactly the same way it
accepts an App Registration's — nothing in the workflow file needs to
change either way.

Two limits worth knowing before you rely on this: a single identity
(App Registration or UAMI) can hold at most 20 federated credentials, so
there's plenty of room if you later add more subjects; and unlike an App
Registration, a UAMI cannot itself be granted admin-consent-requiring
API permissions (Microsoft Graph, etc.) — irrelevant to this lab, since
the `deploy-*` jobs only ever need Azure Resource Manager roles, not
Graph permissions, but worth knowing if you reuse this identity for
something else later.

#### 0.9.b — Shared evaluation environment, by hand (skip entirely if you're leaving `cloud-eval` off)

Do this section only if you decided, in the checklist above, that you
want Part 4.3's live CI demonstration badly enough to pay for it. If
not, skip straight to 0.9.c — there is nothing else in this subsection
that any other part of the lab depends on.

`cloud-eval` runs on every pull request against a **persistent shared
eval environment** — deliberately not any of the three `nimbus-dev` /
`nimbus-staging` / `nimbus-production` environments 0.9.a's credentials
deploy to, so that a PR under review can never call an environment
that's also serving deployed traffic. Stand it up once:

```bash
azd env new nimbus-eval
azd up
```

**Now create the eval identity itself.** Unlike 0.9.a, there's no `azd
pipeline config` command that does this for you — `cloud-eval` isn't a
`deploy-*` job azd knows about, so this identity, its federated
credential, and its access grants are by-hand steps.

**Use a user-assigned managed identity (UAMI) — that's the recommended
default here, not just a fallback.** An app registration
(`az ad app create`/`az ad sp create`) needs a Microsoft Entra ID
directory role (Application Administrator / Cloud Application
Administrator) — a separate permission system from ordinary Azure
subscription RBAC, and one plenty of orgs restrict even for accounts
that otherwise have Owner or Contributor. A UAMI only needs the same
subscription/resource-group RBAC `azd provision` already required of
you, so it works in strictly more environments, with no downside for
what this identity needs to do. If you already tried
`az ad app create`/`az ad sp create` and hit `Insufficient privileges to
complete the operation`, that confirms it — use the commands below
instead, not sudo or an admin's help:

```bash
azd env select nimbus-eval
EVAL_RG=$(azd env get-values | grep AZURE_RESOURCE_GROUP | cut -d'=' -f2- | tr -d '"')

# 1. The identity itself -- lives inside nimbus-eval's OWN resource group
#    on purpose, not a separate untouched one like 0.9.a′'s CI-deploy
#    identity: when you eventually tear nimbus-eval down (`azd down
#    --purge`, per the Cleanup section), this identity should go with
#    it -- there's nothing left for it to authenticate to once
#    nimbus-eval is gone.
az identity create --name nimbus-eval-cloudeval --resource-group "$EVAL_RG"

# 2. Federated credential trusting pull_request runs from this repo -- a
#    different subject than 0.9.a's main-branch credential, which is
#    exactly why this needs its own identity, not a shared one; see the
#    subject-matching explanation above.
az identity federated-credential create \
  --name github-pull-request \
  --identity-name nimbus-eval-cloudeval \
  --resource-group "$EVAL_RG" \
  --issuer 'https://token.actions.githubusercontent.com' \
  --subject 'repo:<org>/<repo>:pull_request' \
  --audiences 'api://AzureADTokenExchange'

# 3. Grant it exactly what the eval scripts call directly -- resource-group
#    scope on nimbus-eval only, not subscription-wide like 0.9.a′'s deploy
#    identity. This identity never touches Foundry directly (the model
#    call goes through the LiteLLM gateway with its own key, step 4
#    below), so it only needs: read/analyze on Content Safety, read-only
#    query on Search, and read on the one Key Vault secret that holds the
#    gateway key. --assignee-object-id plus --assignee-principal-type
#    sidesteps a common transient error ("principal does not exist in
#    the directory") right after creating a brand-new identity, caused
#    by ordinary AAD replication lag.
PRINCIPAL_ID=$(az identity show --name nimbus-eval-cloudeval --resource-group "$EVAL_RG" --query principalId -o tsv)
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
SCOPE="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$EVAL_RG"

az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal --role "Cognitive Services User" --scope "$SCOPE"
az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal --role "Search Index Data Reader" --scope "$SCOPE"
az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal --role "Key Vault Secrets User" --scope "$SCOPE"

# 4. EVAL_AZURE_CLIENT_ID is the identity's CLIENT id, not its
#    principal/object id from step 3 -- they are different values
echo "EVAL_AZURE_CLIENT_ID: $(az identity show --name nimbus-eval-cloudeval --resource-group "$EVAL_RG" --query clientId -o tsv)"
echo "EVAL_AZURE_TENANT_ID: $(az account show --query tenantId -o tsv)"
echo "EVAL_AZURE_SUBSCRIPTION_ID: $SUBSCRIPTION_ID"
```

These three roles are the same ones `infra/resources.bicep` grants the
app's own managed identity for these exact operations (`cognitiveServicesUserRoleId`,
`searchIndexDataReaderRoleId`, `keyVaultSecretsUserRoleId`) — this
identity isn't getting any access the deployed app itself doesn't
already have. `ai-release.yml`'s `cloud-eval` job authenticates with
`azure/login` using OIDC, which accepts a UAMI's client ID exactly the
same way it would accept an app registration's — nothing in the workflow
file cares which identity type is behind it.

##### 0.9.b′ — Alternative: an app registration, if your tenant allows it

If your account does have the Entra ID directory role an app
registration needs, and you'd rather use one — for consistency with an
existing process, say — this does the same job as the commands above,
**instead of** them, not in addition:

```bash
azd env select nimbus-eval
EVAL_RG=$(azd env get-values | grep AZURE_RESOURCE_GROUP | cut -d'=' -f2- | tr -d '"')

# 1. A separate app registration from 0.9.a's -- it needs a federated
#    credential with a different subject (pull_request, not main), which
#    means it has to be a different app registration; see the
#    subject-matching explanation above.
APP_ID=$(az ad app create --display-name nimbus-eval-cloudeval --query appId -o tsv)
az ad sp create --id "$APP_ID"   # app registrations need a service principal before they can hold role assignments

# 2. Federated credential trusting pull_request runs from this repo
az ad app federated-credential create \
  --id "$APP_ID" \
  --parameters '{
    "name": "github-pull-request",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:<org>/<repo>:pull_request",
    "audiences": ["api://AzureADTokenExchange"]
  }'

# 3. Same three roles as the UAMI path above
SP_OBJECT_ID=$(az ad sp show --id "$APP_ID" --query id -o tsv)
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
SCOPE="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$EVAL_RG"

az role assignment create --assignee "$SP_OBJECT_ID" --role "Cognitive Services User" --scope "$SCOPE"
az role assignment create --assignee "$SP_OBJECT_ID" --role "Search Index Data Reader" --scope "$SCOPE"
az role assignment create --assignee "$SP_OBJECT_ID" --role "Key Vault Secrets User" --scope "$SCOPE"

# 4. The three values the workflow needs as EVAL_AZURE_CLIENT_ID etc.
echo "EVAL_AZURE_CLIENT_ID: $APP_ID"
echo "EVAL_AZURE_TENANT_ID: $(az account show --query tenantId -o tsv)"
echo "EVAL_AZURE_SUBSCRIPTION_ID: $SUBSCRIPTION_ID"
```

Then in GitHub, **Settings → Secrets and variables → Actions**, add:

| Add | Kind | Value |
|---|---|---|
| `EVAL_AZURE_CLIENT_ID` / `_TENANT_ID` / `_SUBSCRIPTION_ID` | secret | The three values step 4 above printed (either path — UAMI or app registration) |
| `EVAL_AZURE_OPENAI_DEPLOYMENT`, `EVAL_AZURE_SEARCH_ENDPOINT`, `EVAL_AZURE_CONTENT_SAFETY_ENDPOINT`, `EVAL_LLM_GATEWAY_ENDPOINT`, `EVAL_AZURE_KEY_VAULT_NAME` | variable | `azd env get-values` from the `nimbus-eval` environment you just created — the matching keys are `AZURE_OPENAI_DEPLOYMENT`, `AZURE_SEARCH_ENDPOINT`, `AZURE_CONTENT_SAFETY_ENDPOINT`, `LLM_GATEWAY_ENDPOINT`, `AZURE_KEY_VAULT_NAME` |

**Why there's no `EVAL_AZURE_OPENAI_ENDPOINT`:** it would be natural to
assume the eval job needs Foundry's own endpoint, but nothing in this
codebase ever reads that value — the agent talks to the LiteLLM gateway
only, never straight to Foundry (`app/agent/azure_openai_client.py`).
`EVAL_LLM_GATEWAY_ENDPOINT` and the gateway's key (fetched at run time
from Key Vault in `ai-release.yml`, using `EVAL_AZURE_KEY_VAULT_NAME`)
are what actually let `cloud-eval` make a real model call — leaving
either of those two out is what makes `cloud-eval` fail on its first
real run with `RuntimeError: LLM_GATEWAY_ENDPOINT is not set`, which is
exactly the shape of bug that stays invisible while `ENABLE_CLOUD_EVAL`
is off and only surfaces the moment you turn it on.

**Why some of these are Secrets and others are Variables, specifically:**
GitHub Actions Secrets are encrypted at rest, never displayed again once
saved (even to you), and automatically masked (`***`) if they ever show
up in a job log. Variables are stored and displayed as plain text in the
repo settings UI and in logs. The client/tenant/subscription IDs go in
as Secrets because they identify a credential that can authenticate as
this identity — worth masking even though an ID alone isn't a
usable secret by itself, same caution as the `AZURE_CLIENT_ID` etc. pair
`azd pipeline config` wrote in 0.9.a. The endpoint URLs and deployment
name go in as Variables because they're just configuration — knowing an
endpoint URL grants nobody access to anything, and having them render in
plain text in the Actions UI makes a misconfigured-endpoint failure much
faster to spot during class than clicking into a masked secret to guess
what's in it. If you're ever unsure which kind a new value belongs in,
default to Secret for anything that grants access and Variable for
anything that's just describing where or how to connect.

**Last step, and the one that actually turns spending on:** add one more
Variable, `ENABLE_CLOUD_EVAL` = `true`. Everything above this point in
0.9.b — the environment, the credentials — can exist without costing
anything per PR; it's this flag, and only this flag, that makes
`cloud-eval` actually start running (and billing) on every pull request
against the repo. Setting the `EVAL_*` secrets and variables
without also setting this one leaves `cloud-eval` correctly skipped, so
if you want to stage this (stand up the environment now, turn on the
spend later, right before the class session that uses it), that's a
safe, supported way to do it — just don't forget the last step, or
Part 4.3 will show every check skipped instead of the HOLD/PROMOTE it's
supposed to demonstrate.

#### 0.9.c — GitHub Environments, by hand

**Settings → Environments**: create three environments named exactly
`development`, `staging`, `production` (the names must match the
`environment:` keys in `.github/workflows/ai-release.yml`'s `deploy-*`
jobs exactly, or that job fails to find its environment). Add a
**required reviewer on all three** — `development` and `staging`, not
just `production`.

This is a deliberate cost control, and it's worth understanding why
before you skip it as "just do production." Each of the three
environments provisions its own real Foundry deployment, Azure AI
Search service, and Azure AI Content Safety account (see ADR 0001's
sizing table) — three times the standing Azure spend of one environment,
not a fraction of it. `deploy-staging` needs `deploy-dev`, and
`deploy-production` needs `deploy-staging`, so the three jobs already
only ever run one at a time, in order — but "in order" and "gated"
aren't the same thing. Without a required reviewer on `development` and
`staging`, **every merge to `main`** provisions or updates both of those
two environments automatically, with nobody in the loop, and only pauses
for a human at the very last step. With a required reviewer on all
three, a merge to `main` queues `deploy-dev` but goes no further until
someone approves it — same for the step from dev into staging, and
staging into production — so nothing spends money until a person looks
at that specific promotion and decides it's worth it. For a class that
might repeat Part 4.3 more than once, this is the difference between "one
approved deploy" and "N merges × two unattended environment provisions
each" by the time everyone's done.

Optional but recommended: **Settings → Branches**, protect `main`, and
require the four PR checks (`deterministic-tests`, `code-scanning`,
`cloud-eval`, `release-gate`) before merge is allowed.

Full explanation of the credential architecture (which job uses which
kind of Azure credential, and why): README.md "CI/CD and cloud
credentials."

#### 0.9.d — Verify the pipeline end-to-end (do this before class, not during it)

Only relevant if you turned `ENABLE_CLOUD_EVAL` on in 0.9.b — if you left
`cloud-eval` off on purpose, there's nothing to dry-run here: every job
downstream of it will just show skipped, and that's the correct, expected
result, not something to troubleshoot.

0.9.a–0.9.c are all "did I configure this correctly" steps — none of
them prove the pipeline actually runs. This step does, the same way
Part 0.8's `verify_deployment.py` proves Track A's sandbox actually
works rather than just existing. Do this from a scratch branch, using
exactly the mechanism Part 4.3 uses in class, so a failure here is the
same failure trainees would otherwise hit live:

1. Create a throwaway branch, set `PROMPT_VERSION: candidate_broken` in
   `.github/workflows/ai-release.yml`'s `env:` block, commit, push, and
   open a PR against `main`.
2. Watch all four PR-time checks. Expected: `deterministic-tests` and
   `code-scanning` pass (offline, always should); `cloud-eval` passes its
   own steps and produces real numbers (if this fails, it's row 4 or 5 of
   the checklist above — `nimbus-eval` isn't live, or the eval
   credential/subject is wrong); `release-gate` reports **HOLD** with a
   `tool_selection_accuracy` breach, and posts that as a PR comment. A
   HOLD here is correct and expected — `candidate_broken` is *supposed*
   to fail. What you're proving is that the jobs ran and produced the
   right decision, not that everything passes.
3. Change `PROMPT_VERSION` to `baseline` (or `candidate_fixed`), push
   again to the same PR. Expected: all four checks now pass, `release-gate`
   reports **PROMOTE**.
4. Merge the PR. Expected: `deploy-dev` appears in the PR's
   **Deployments** view in a **Waiting** state — if it fails immediately
   instead of waiting, row 2 or row 3 is wrong (bad credential, or a
   region with no `gpt-5-mini` capacity). Approve it. Expected: it runs
   `azd provision`/`azd deploy`/the canary rollout and succeeds, `nimbus-dev`
   now exists. Approve `deploy-staging`, then `deploy-production`, the
   same way — each should wait for its own separate approval (row 7) and
   then succeed.
5. Revert `PROMPT_VERSION` back to `baseline` in `main` afterward, and
   optionally `azd down --purge` the three environments this smoke test
   created if you don't want them standing (billing) through the rest of
   the class — trainees don't need `nimbus-dev`/`staging`/`production` to
   exist for anything in Parts 1–5, only Part 4.3 exercises them, and
   that step recreates them itself on demand.

If every step above behaved as described, the pipeline is proven end to
end and Part 4.3 will work the same way live. If something didn't match
the expected result, match the symptom back to the checklist row it
maps to rather than re-running the whole thing and hoping — a partial
CI/CD setup fails in ways that look similar (a red X on a job) but come
from very different rows.

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

### 4.3 — See it enforced in CI, not just locally (optional — skip if your class left `cloud-eval` off for cost reasons)

This step only produces the result described below if an admin turned
`ENABLE_CLOUD_EVAL` on (0.9.b) — see the note at the top of Part 0 for
why that's off by default and not required for anything else in this
guide. If you're not sure whether your class did, check the repo's
**Settings → Secrets and variables → Actions → Variables** tab for
`ENABLE_CLOUD_EVAL = true`, or just open a PR and see whether
`cloud-eval` runs or shows skipped. Skipped is not a failure on your
part — it means this step was left off deliberately, and Part 5's
deliverables (already produced in Parts 2–4, entirely locally) don't
need it.

Push a branch that sets `PROMPT_VERSION: candidate_broken` in
`.github/workflows/ai-release.yml`'s `env:` block and open a PR — the
`deterministic-tests`, `code-scanning`, `cloud-eval`, and `release-gate`
jobs all run on every PR (assuming `ENABLE_CLOUD_EVAL` is on — otherwise
the latter two show as skipped, and everything below in this section
describes what you'd see if it were on). `code-scanning` is a clearly-labeled
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
merge to `main`, three deploy jobs queue up in sequence:
`deploy-dev` → `deploy-staging` (only after dev succeeds) →
`deploy-production` (only after staging succeeds). **Every one of the
three**, not just production, is gated behind its own GitHub
Environment's required-reviewer approval (see 0.9.c) — a deliberate cost
control, since each environment is a real, separately-billed Foundry +
Search + Content Safety stack, not a free promotion step. Go to the PR's
**Deployments** view (or the Actions run) and you'll see `deploy-dev`
sitting in a "Waiting" state until someone with reviewer rights clicks
**Review deployments → Approve**; only then does it actually provision
or update anything, and only then does `deploy-staging` even queue up to
wait for its own approval, and likewise for `deploy-production` after
that. All three promote the exact same built image (this commit's SHA)
through `nimbus-dev` → `nimbus-staging` → `nimbus-production` — see
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
| `azd up` fails at the OpenAI resource with a quota/capacity error | No `gpt-5-mini` capacity in your chosen region | Pick a different region: `azd env set AZURE_LOCATION <region>` then `azd up` again — it's idempotent, already-created resources are left alone |
| `azd up` fails with `InvalidResourceProperties: The specified SKU 'Standard' ... is not supported by the model` | `gpt-5-mini` only offers GlobalStandard/DataZoneStandard, never plain regional Standard | This is already fixed in `infra/resources.bicep`'s `chatDeployment` — if you see this, you're on an older copy of the template; pull the current one |
| `azd up` fails with a role-assignment permission error | Your account has Contributor but not User Access Administrator | Ask your Azure admin to grant it, or have them run `azd provision` once on your behalf |
| `azd up`/`azd provision` fails on the Foundry **project** with `BadRequest: Unsupported configuration. To create projects, you must enable a managed identity on your resource` — even though `az cognitiveservices account show --name <foundry-name> --resource-group <rg> --query identity` confirms `SystemAssigned` is already there | A real, intermittent Azure race: the account's identity is visible in ARM before the Cognitive Services resource provider's own internal state (what project creation actually checks) has caught up. Retrying the same combined deployment immediately often fails again for the same reason | Do a two-phase provision instead: `azd env set DEPLOY_FOUNDRY_PROJECT false && azd provision` (creates/confirms the account and its identity, skips the project), wait a few minutes, then `azd env set DEPLOY_FOUNDRY_PROJECT true && azd provision` (a genuinely separate deployment that creates just the project). `infra/main.bicep`'s `deployFoundryProject` param exists specifically for this — see its doc-comment. Set it back to unset/`true` afterward so future `azd up` runs go back to the normal single-pass path |
| `azd deploy` fails to build the Docker image | Docker daemon not running locally | Start Docker Desktop / `sudo systemctl start docker`, or switch `docker.remoteBuild: true` under `services.api` in `azure.yaml` to build in ACR instead of locally |
| `python scripts/verify_deployment.py` fails the health check with `The read operation timed out` | Two different causes, and the logs tell you which: (1) `containerAppMinReplicas` defaults to `0` (scale-to-zero) — the very first request cold-starts the container, which can outlast the script's 15-second timeout even though `/healthz` itself is cheap; or (2) `azd deploy` never actually ran (e.g. `azd up` aborted earlier at the postprovision hook), so the Container App is still running `infra/resources.bicep`'s bootstrap placeholder image (`mcr.microsoft.com/azuredocs/containerapps-helloworld`), which listens on port 80, not the real app's port 8000 — ingress can never get a response on the configured target port, so every request just hangs | Run `az containerapp logs show --name $(azd env get-values \| grep AZURE_CONTAINER_APP_API_NAME \| cut -d'=' -f2- \| tr -d '"') --resource-group $(azd env get-values \| grep AZURE_RESOURCE_GROUP \| cut -d'=' -f2- \| tr -d '"') --follow` first. If you see the real app's uvicorn startup banner, it's just cold start — retry the verify command in 30-60s. **If you instead see `Listening on :80...`, that's the placeholder image talking, not your app** — `azd deploy` hasn't overwritten it yet. Fix: just run `azd deploy` (or `azd up` again) to build and push the real image |
| `azd deploy`/`azd up` can't find a target for the `gateway` service, or the gateway Container App stays on the placeholder image no matter how many times you redeploy | A real bug in `infra/resources.bicep`: the `litellmGateway` resource was missing the `azd-service-name: gateway` tag that `azd` uses to match `azure.yaml`'s `gateway` service to an actual Azure resource — without it, `azd deploy` has no way to find that Container App at all, so it can never overwrite its bootstrap placeholder image | Already fixed — `litellmGateway` now carries `tags: union(tags, { 'azd-service-name': 'gateway' })`, same pattern the `api` container app already had. If you're on an older copy of `resources.bicep`, pull the current one, run `azd provision` to apply the tag (a non-disruptive metadata update, not a resource recreation), then `azd deploy` to push the real gateway image |
| `python scripts/verify_deployment.py` fails the RAG-grounding check | The Search index wasn't seeded | Re-run the postprovision hook manually: `python -m scripts.build_search_index` |
| `python scripts/verify_deployment.py` fails the guardrail check | Content Safety endpoint not reachable, or `content_safety.py` raised before returning | Check `az containerapp logs show` for the actual exception; confirm `AZURE_CONTENT_SAFETY_ENDPOINT` is set on the Container App with `azd env get-values \| grep CONTENT_SAFETY` |
| Azure OpenAI calls are slow / rate-limited during a full class running eval scripts simultaneously | Shared quota across trainees deploying to the same subscription | Each trainee should provision their own `azd env` (their own Azure OpenAI deployment) rather than sharing one — see Part 0.5 |
| `azd up`'s postprovision hook fails with `ERROR: Could not install packages due to an OSError: [Errno 13] Permission denied: '/usr/lib/python3.12/site-packages/...'` | Your Linux user doesn't own the system Python's site-packages directory. `--break-system-packages` only bypasses pip's "externally managed environment" check — it doesn't grant you write access to a system directory you don't own | This is already fixed in `azure.yaml`'s `postprovision` hook (`pip install --user ...`) — if you're on an older copy, pull the current one. To unblock right now without re-pulling: run `pip install --user -r requirements.txt --break-system-packages --quiet` yourself in the repo root, then just re-run `azd up` — it's idempotent (already-created resources are left alone), it'll re-run the postprovision hook, which now succeeds instantly since the dependencies are already installed, and then continue on to the `azd deploy` step that hadn't run yet |

There is no local/offline fallback path anywhere in this lab. If a
command fails because of a cloud service, the fix is always to fix the
cloud configuration (region, quota, RBAC, endpoint), never to fall back
to a stub — there isn't one.

---

## What's next

Day 2 picks up exactly where this environment is left — no re-provisioning,
no `azd down`. Leave `azd env get-values` loaded in your shell (or re-run
Part 0.7 if you start a fresh terminal) and go to `Day2_Lab_Guide.md`.
