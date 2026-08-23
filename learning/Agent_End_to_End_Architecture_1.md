# Agent End-to-End Architecture — Nimbus Support Copilot

**What this document is:** the single consolidated reference for how
this system actually works, start to finish — every component, every
request's full lifecycle, and the security model. Day 1 and Day 2's lab
guides teach these pieces one lab at a time, tied to specific exercises;
this document is what you'd hand to an interviewer, or re-read six months
from now, to explain the whole system in one sitting without hunting
through two days of lab steps to reassemble it.

Every fact below is read directly from the code, not summarized from
memory — file paths and line-level behavior are called out so you can
verify anything here yourself.

---

## 1. The system in one paragraph

Nimbus Support Copilot is a FastAPI service that answers Nimbus Telecom
customers' product, plan, billing, and refund questions. Every request
runs through a fixed pipeline: retrieve relevant policy documents from a
real Azure AI Search index, decide which of five tools are even allowed
this turn (a deterministic policy layer plus a real Azure AI Content
Safety input check), let a **ManagerAgent** — one real Microsoft Foundry
function-calling call, restricted to two routing functions and no real
tools at all — decide whether this turn belongs to BillingAgent or
AccountAgent, let that **specialist** — a second real Foundry
function-calling call, scoped to only its own tools — pick a tool from
what's allowed, execute that tool call at a third, independent
deterministic boundary, generate a grounded answer from the retrieved
documents and the tool result, and estimate the turn's token cost. Every
step that can call a cloud service does, on every single request. There
is no environment variable, no `stub`/`local` default, and no offline
mode anywhere in this system — that is the single most important
architectural fact about the whole system, and Section 2 below walks
through exactly what that means in practice. See
`docs/adr/0001-foundry-and-multiagent.md` for the full reasoning behind
the Foundry choice and the Manager/Specialist split, and Section 5
below for the agent architecture in detail.

---

## 2. Every cloud call is real — nothing to toggle, nothing to fall back to

Every cloud integration point in this system — the model calls,
retrieval, and content-safety checks — has exactly one code path, and
it's the real one. There's no environment variable that swaps in a
local stand-in, and no default that quietly degrades to something
cheaper or offline. If the real Azure credentials aren't configured, the
system doesn't fall back to anything: it fails immediately, with a
`RuntimeError` that names the exact missing environment variable.

That matters because a fallback is only safe if everyone always notices
they're on it. A trainee, a CI job, or a deployed environment that's
missing one piece of configuration should never be able to run —
quietly, successfully, with no error — against a weaker version of the
system than the one that actually ships. Making the real path the
*only* path removes that risk entirely. The cost is that every
exercise, evaluation, and CI job that touches these three modules now
needs live Azure credentials to run at all — there's no free, offline
way to exercise them.

Concretely, here's what each of the three integration points does:

- **Model calls** (`app/agent/azure_openai_client.py`) — a real
  `openai.OpenAI`-compatible client factory, pointed at the LiteLLM
  Proxy gateway (`infra/resources.bicep`'s `litellmGateway` Container
  App), not at Microsoft Foundry directly — see
  `docs/adr/0002-llm-gateway-and-observability.md`. The gateway is what
  actually authenticates to Foundry, using the same managed identity
  every other client in this repo uses. `manager_agent.route()` and
  each specialist's `decide()` all call it — one shared client factory,
  three callers.
- **Retrieval** (`app/retrieval/retrieval.py`) — a real Azure AI Search
  `SearchClient`. `retrieve()` always queries it; `check_freshness()`
  always compares against the live index, never a local file.
- **Content safety** (`app/agent/content_safety.py`) — a real
  `ContentSafetyClient.analyze_text()` call. `check_content()` always
  calls it.

One practical thing worth knowing up front: every lab exercise and
structural check that never touches these three modules (`tests/unit/`,
`tests/validate_*.py`) still runs free and offline, because it simply
doesn't call them. But every evaluation script and every CI job that
*does* exercise the agent (`eval/run_*.py`, the `cloud-eval` GitHub
Actions job) needs live Azure credentials to run — see `README.md`'s
"CI/CD and cloud credentials" section for how that's provisioned once,
for a shared CI environment, not per PR.

---

## 3. Full request lifecycle

```
 Customer / trainee
        │
        │ POST /chat  {message, customer_id, prompt_version}
        ▼
 app/api/main.py  (FastAPI)
        │  - configure_azure_monitor() at startup IF deployed with
        │    APPLICATIONINSIGHTS_CONNECTION_STRING set (every real
        │    deployment via `azd up` has this; a bare `uvicorn --reload`
        │    without it is a complete no-op here, not a broken import)
        │  - customer_id is trusted exactly as given -- see Section 7
        ▼
 app/agent/agent.py :: run_turn()
        │
        ├─ SPAN 1  retrieval_query
        │     retrieval.retrieve(user_message, k=3)
        │     Always: real Azure AI Search SearchClient.search()
        │     (BM25 keyword search -- see that module's docstring for
        │     why this is a named scoping decision, not an oversight)
        │
        ├─ SPAN 2  agent_policy_layer
        │     tool_policy.agent_policy_layer(user_message)
        │     - input_layer_classify(): regex injection heuristic
        │       (probabilistic, audit/UX signal only)
        │     - decides which of the 5 tools are even OFFERED this turn
        │       (the credit tool needs a credit/refund/compensation
        │       trigger phrase present, or it's never offered)
        │
        ├─ SPAN 2.5  content_safety_check
        │     content_safety.check_content(user_message)
        │     Always: real Azure AI Content Safety analyze_text()
        │     (severity >= 2 on any category flags)
        │     -> if flagged, EVERY tool is withheld, deterministically,
        │        before the model is ever asked
        │
        ├─ SPAN 3  manager_route
        │     manager_agent.route(user_message)
        │     Always: ONE real function-calling call through the LiteLLM
        │     gateway to Foundry (see Section 2's client-factory table
        │     and docs/adr/0002-llm-gateway-and-observability.md),
        │     restricted to exactly two functions (delegate_to_billing,
        │     delegate_to_account) -- the manager never sees the real
        │     tool schemas and structurally cannot call a real tool.
        │     Defaults to "account" (the specialist with no
        │     monetary-capable tool) if the model's decision is missing
        │     or unclear -- a deliberate safety default.
        │
        ├─ SPAN 4  specialist_tool_selection
        │     billing_agent.decide(...) OR account_agent.decide(...)
        │     (whichever SPAN 3 routed to)
        │     Always: a SECOND real function-calling call through the
        │     same gateway, restricted to the intersection of that specialist's own
        │     tool set (tool_schemas.BILLING_TOOL_NAMES /
        │     ACCOUNT_TOOL_NAMES) and whichever tools survived spans 2/2.5
        │     -- customer_id is injected into the returned arguments by
        │        code, after the call, never trusted from model output
        │
        ├─ SPAN 5  tool_execution   (only if a tool was selected)
        │     tool_policy.enforce_tool_execution_boundary(name, args,
        │                            session_customer_id=customer_id)
        │     - the ONE place any of the 5 real tools (app/agent/tools.py)
        │       actually run, regardless of which specialist selected it
        │     - re-validates session_customer_id against the arguments,
        │       independent of anything the model decided
        │     - request_customer_credit enforces MAX_CREDIT_AMOUNT_USD=50
        │       and a 3-per-customer-per-day limit IN CODE, and only ever
        │       writes a PENDING_APPROVAL record -- never a terminal
        │       executed state
        │
        ├─ SPAN 6  generate_answer
        │     agent._generate_answer(user_message, chunks, tool_result)
        │     -- template composition, NOT a third model call -- builds
        │        the answer text + citation list directly from whatever
        │        was actually retrieved in span 1 and returned in span 5,
        │        so every citation is exactly traceable and there is no
        │        additional hallucination surface (see that function's
        │        docstring for the explicit trade-off this is)
        │
        ├─ (not a span) cost_tracking.estimate_turn_cost(...)
        │     ~4-chars-per-token heuristic estimate, NOT billed usage
        │     -- see Section 6 for the honesty note this module insists on
        │
        └─ writes eval/traces/trace-<id>.json (redacted span summaries,
           truncated to 200 chars each) UNLESS persist_trace=False (every
           eval/*.py script sets this, since they run many turns per run)
        ▼
 JSON response: answer, citations, routed_specialist, tool_call,
 tool_result, content_safety_flagged, content_safety_categories,
 estimated_cost_usd, estimated_tokens, tool_error, injection_flagged,
 step_count, prompt_version
```

Every one of the six real spans above calls a real service on every
single request. That means this pipeline behaves identically in a lab
exercise and in production, because it's the same code, byte for byte,
in both — the only thing that changes between your own sandbox, the
shared CI eval environment, and any of the three deployed environments
(`nimbus-dev`/`nimbus-staging`/`nimbus-production`) is which Azure
resource's endpoint the environment variables point at. Two real model
calls happen per turn, not one — the manager routes, then the
specialist decides — and that's a real cost/latency trade-off, named
plainly in `docs/adr/0001-foundry-and-multiagent.md` rather than glossed
over.

---

## 4. Component map — every file, one line each

**API / entry point**
| File | Role |
|---|---|
| `app/api/main.py` | FastAPI app: `/chat`, `/healthz`, `/approvals/*`; the only place real Application Insights telemetry is turned on |
| `Dockerfile` | Builds the deployable image. Bakes in no index and no cloud config — those come from the real Search service and injected env vars at deploy/run time, not build time |

**Orchestration**
| File | Role |
|---|---|
| `app/agent/agent.py` | `run_turn()` — the pipeline in Section 3, trace emission, the `_generate_answer()` template composer |

**The agents (the real model layer)**
| File | Role |
|---|---|
| `app/agent/manager_agent.py` | ManagerAgent: `route()` — one real call through the LiteLLM gateway to Foundry, restricted to two routing functions, no tool access, defaults to "account" when unclear |
| `app/agent/billing_agent.py` | BillingAgent specialist: `decide()` — scoped to `retrieve_latest_bill`/`create_support_ticket`/`request_customer_credit`, `PROMPT_VERSIONS` (baseline/candidate_broken/candidate_fixed) |
| `app/agent/account_agent.py` | AccountAgent specialist: `decide()` — scoped to `get_customer_plan`/`check_network_outage`/`create_support_ticket`, no monetary tool at all |
| `app/agent/tool_schemas.py` | Shared master `TOOLS_SCHEMA` (all 5 tools), `BILLING_TOOL_NAMES`/`ACCOUNT_TOOL_NAMES` subsets, `MANAGER_ROUTING_SCHEMA` |
| `app/agent/prompt_loader.py` | Shared `load_prompt(file_stub)` — front-matter parsing used by all three agent roles |
| `app/agent/azure_openai_client.py` | Real `openai.OpenAI`-compatible client factory (pointed at the LiteLLM gateway, not Foundry directly — `docs/adr/0002-llm-gateway-and-observability.md`) + deployment-name lookup — no other code in this module |
| `app/prompts/system_prompt_manager.md` | Manager's routing-only prompt |
| `app/prompts/system_prompt_account.md` | AccountAgent's production prompt |
| `app/prompts/system_prompt_billing_baseline.md` | BillingAgent's production prompt, incl. the disambiguation clause |
| `app/prompts/system_prompt_billing_candidate_broken.md` | Day 1 lab's injected fault — disambiguation clause removed |
| `app/prompts/system_prompt_billing_candidate_fixed.md` | Day 1 lab's corrected candidate |

**Guardrails / policy**
| File | Role |
|---|---|
| `app/agent/tool_policy.py` | `agent_policy_layer()` (which tools are offered) + `enforce_tool_execution_boundary()` (the deterministic authorization boundary) + `approve_credit_request()` (human-only approval path) |
| `app/agent/content_safety.py` | Input-layer content moderation — always real Azure AI Content Safety, no stub |
| `app/agent/tools.py` | The 5 real tool implementations + `MAX_CREDIT_AMOUNT_USD` / daily-limit constants |
| `app/agent/cost_tracking.py` | Per-turn token/cost estimation |

**Retrieval**
| File | Role |
|---|---|
| `app/retrieval/retrieval.py` | Real Azure AI Search only: `retrieve()`, `list_indexed_doc_ids()`, `check_freshness()`, `expected_live_doc_ids()` |
| `knowledge_docs/*.md` | The synthetic policy corpus (front-matter + body) the index is built from |

**Evaluation / release gate** — see `Dataset_and_Evaluation_Guide.md` for full detail
| File | Role |
|---|---|
| `eval/run_retrieval_smoke.py`, `run_llm_eval.py`, `run_agent_trajectory_eval.py`, `run_safety_regression.py` | Layers 2-4, all against real cloud calls |
| `eval/apply_release_policy.py`, `gate_or_fail.py`, `post_pr_summary.py` | The release-policy gate and its CI-facing wrappers |
| `eval/check_index_freshness.py`, `validate_datasets.py` | Supporting checks (the former against the real index, the latter fully offline) |
| `tests/validate_prompt_templates.py`, `tests/validate_tool_schemas.py`, `tests/unit/*` | Fully offline, structural checks only |
| `release-policy.yaml` | The declarative threshold policy `apply_release_policy.py` evaluates against |

**Infrastructure / deployment**
| File | Role |
|---|---|
| `azure.yaml` | `azd` manifest: service definition + the postprovision hook that builds the Search index |
| `infra/main.bicep` | `azd` subscription-scope entry point: creates the resource group, deploys `resources.bicep` |
| `infra/resources.bicep` | All real Azure infrastructure (Section 5) |
| `scripts/build_search_index.py` | Creates/repairs the real Search index schema + contents |
| `scripts/inject_stale_doc.py` | Trainer-only Day 2 fault injector, against real cloud state |
| `scripts/verify_deployment.py` | Automated post-deploy smoke test against a live Container App |
| `scripts/seed_lab.sh` | Free, offline pre-flight checks; points at `azd up` |
| `.github/workflows/ai-release.yml` | The full CI/CD pipeline — see `Day1_Lab_Guide.md` Part 4 and `README.md`'s "CI/CD and cloud credentials" |

---

## 5. Manager/Specialist multi-agent design

This system routes every turn through two separate real model calls: a
`ManagerAgent` that only decides "billing or account," then a second
call to whichever specialist it routed to. See
`docs/adr/0001-foundry-and-multiagent.md` for the full decision record;
this section is the architectural detail.

The split exists because one agent juggling both billing and account
logic in a single prompt means every rule for one domain has to coexist
with every rule for the other, competing for the same model's attention
in the same instructions. Separating the routing decision from the
domain-specific decision means BillingAgent's rules never have to share
space with AccountAgent's — each specialist only ever has to reason
about its own tools and its own policy. The cost is a second model call
per turn, and that trade-off is named explicitly below rather than left
implicit.

**ManagerAgent** (`app/agent/manager_agent.py`, `system_prompt_manager.md`)
— routing only. Its model is offered exactly two functions
(`delegate_to_billing`, `delegate_to_account`, see
`tool_schemas.MANAGER_ROUTING_SCHEMA`) and nothing else; it never sees
`TOOLS_SCHEMA` and cannot construct a real tool call under any prompt or
input. If its decision is missing, malformed, or anything other than
exactly one of the two known functions, `route()` returns `"account"` —
a deliberate fail-safe, since AccountAgent has no monetary-capable tool
at all.

**BillingAgent** (`app/agent/billing_agent.py`, three prompt versions)
— `retrieve_latest_bill`, `create_support_ticket`,
`request_customer_credit`. This is the only specialist that can ever
reach the credit tool, and is where Day 1's disambiguation fault lives.

**AccountAgent** (`app/agent/account_agent.py`, one prompt) —
`get_customer_plan`, `check_network_outage`, `create_support_ticket`. No
monetary-capable tool at all, by construction — this is precisely why
"route here when the manager is unsure" is a safe default rather than
just an error fallback.

`create_support_ticket` is the one tool both specialists share — either
can escalate to a human.

**The guardrail architecture underneath — the two-layer `tool_policy.py`
boundary — is completely unchanged by which agent is deciding.** Every
specialist only ever receives a tool schema that has already survived
`agent_policy_layer()` + `content_safety.check_content()` (Section 3,
spans 2/2.5), intersected with that specialist's own static tool subset
(a second, always-on narrowing no prompt or model output can widen).
Multi-agent routing changes *who* decides which tool to call; it never
changes *what's allowed to execute* — `enforce_tool_execution_boundary()`
is the same single function, called the same way, regardless of which
specialist's decision reaches it.

**The honest cost/latency trade-off, named rather than hidden:** this
architecture makes two real model calls per turn (manager + specialist)
where the single-agent design made one. One shared Foundry model
deployment serves all three roles — different system prompts, same
underlying model — to keep the added cost as low as it can be while
still being real. `release-policy.yaml`'s `max_agent_steps` threshold
was revisited for this reason (see its inline note) but not tightened;
it still has headroom for a future multi-hop upgrade (see the ADR's
"Decision" section on the documented, not-yet-built `HandoffBuilder`
upgrade path).

**One-agent extension point, if you ever need to collapse this back
down:** merge `BILLING_TOOL_NAMES` and `ACCOUNT_TOOL_NAMES` back into one
list, give the merged set one system prompt, and have `agent.py` call
that one `decide()` directly instead of `manager_agent.route()` +
per-specialist `decide()`. The two-layer `tool_policy.py` boundary needs
no changes either way — it was designed to be agent-topology-agnostic.

---

## 6. Deployment topology

The diagram below is ONE environment's shape — `infra/resources.bicep`
is one template, deployed three times (`nimbus-dev`, `nimbus-staging`,
`nimbus-production`), each its own resource group, each its own Foundry/
Search/Content Safety instance (no shared quota, no noisy-neighbor risk
between environments). See `docs/adr/0001-foundry-and-multiagent.md`'s
environment-sizing table for exactly how Search tier, Foundry capacity,
and Container App min replicas differ per environment, and
`.github/workflows/ai-release.yml`'s `deploy-dev` / `deploy-staging` /
`deploy-production` jobs for the promotion sequence (automatic / automatic
/ manual-approved) that deploys the same built image through all three.

```
                         ┌─────────────────────────────┐
                         │   GitHub Actions (OIDC)     │
                         │  ai-release.yml deploy-* job │
                         └──────────────┬───────────────┘
                                         │ azd auth login (federated, no stored secret)
                                         ▼
 ┌───────────────────────────────────────────────────────────────────┐
 │  Resource Group (rg-nimbus-<dev|staging|production>)               │
 │                                                                     │
 │  ┌───────────────┐   ┌────────────────┐                            │
 │  │ Log Analytics │◄──┤ Application    │                            │
 │  │ workspace     │   │ Insights       │                            │
 │  └───────────────┘   └───────┬────────┘                            │
 │                               │ telemetry                          │
 │  ┌───────────────┐   ┌───────▼────────────────────────────────┐  │
 │  │ ACR            │◄──┤ Container Apps environment            │  │
 │  │ (admin: OFF)   │   │  ┌──────────────────────────────────┐ │  │
 │  └───────┬────────┘   │  │ Container App (azd-service-name:  │ │  │
 │          │ AcrPull    │  │  api) -- user-assigned managed    │ │  │
 │          └────────────┼─►│  identity, autoscale 20 concurrent│ │  │
 │                        │  │  /replica, minReplicas per env    │ │  │
 │                        │  └──────┬───────────┬───────────┬────┘ │  │
 │                        └─────────┼───────────┼───────────┼──────┘  │
 │                                  │ RBAC       │ RBAC      │ RBAC    │
 │                        ┌─────────▼───┐ ┌──────▼──────┐ ┌──▼──────┐ │
 │                        │  Microsoft  │ │ Azure AI    │ │ Azure AI│ │
 │                        │  Foundry    │ │ Search      │ │ Content │ │
 │                        │ (AIServices │ │  REQUIRED,  │ │ Safety  │ │
 │                        │ + project + │ │  tier per   │ │ REQUIRED│ │
 │                        │ 1 shared    │ │  env        │ │         │ │
 │                        │ deployment) │ │             │ │         │ │
 │                        │  REQUIRED   │ │             │ │         │ │
 │                        └─────────────┘ └─────────────┘ └─────────┘ │
 │                                                                     │
 │  Action Group ── restart / error-rate / latency alerts +           │
 │                  synthetic availability test (see ADR 0003)        │
 └───────────────────────────────────────────────────────────────────┘
```

The diagram above is the overall resource topology, and the `api`
container's connections into Search and Content Safety are exactly as
drawn there. What it doesn't show is the specific path an LLM call
takes: `api` never calls Foundry directly. It calls a second Container
App — the gateway — and the gateway is what actually reaches Foundry.
That path is drawn separately below, since it didn't fit cleanly into
the box art above:

```
 ┌──────────────────────┐   RBAC: Key Vault      ┌───────────────────┐
 │ Container App (api)   │◄──Secrets User─────────┤   Azure Key Vault  │
 │ user-assigned identity│                        │  (RBAC auth, not  │
 └──────────┬─────────────┘                       │   access policies) │
            │ HTTPS + LLM_GATEWAY_API_KEY          │  holds:            │
            │ (bearer key, itself read from        │  - litellm-master- │
            │  Key Vault -- see ADR 0005)           │    key             │
            ▼                                      │  - langfuse-secret-│
 ┌──────────────────────┐   RBAC: Key Vault        │    key             │
 │ Container App          │◄──Secrets User─────────┴───────────────────┘
 │ (gateway -- LiteLLM     │
 │  Proxy, MIT license)    │──RBAC: Cognitive Services OpenAI User──►  Microsoft Foundry
 │ user-assigned identity  │──success_callback: langfuse────────────►  Langfuse Cloud
 └──────────────────────┘                                              (free Hobby tier,
                                                                          traces every call)
```

Both Container Apps share the **same** user-assigned managed identity
this repo has always used — no new identity resource, just two new RBAC
grants on it (Key Vault Secrets User, in addition to the roles below).
See `docs/adr/0002-llm-gateway-and-observability.md` for the gateway
itself and `docs/adr/0005-key-vault-for-gateway-secrets.md` for why the
two secrets above (`litellmMasterKey`, `langfuseSecretKey`) live in Key
Vault rather than as plain Container App secret values.

All three Cognitive Services-family resources (Foundry, Search, Content
Safety) are **required, unconditional resources in `infra/resources.bicep`**
— there is no `deploySearch`/`deployContentSafety` boolean parameter
defaulting them off. Every arrow into them is a **role assignment on the
Container Apps' one shared user-assigned managed identity**, not a stored key:

| Resource | Role (app identity) | Role definition GUID |
|---|---|---|
| ACR | AcrPull | `7f951dda-4ed3-4680-a7ca-43fe172d538d` |
| Microsoft Foundry | Cognitive Services OpenAI User — granted to the **gateway**, not the `api` container, since ADR 0002 | `5e0bd9bd-7b93-4f28-af87-19fc36ad61bd` |
| Azure AI Search | Search Index Data Reader (query-only) | `1407120a-92aa-4202-b7e9-c0e197c71c8f` |
| Azure AI Content Safety | Cognitive Services User | `a97b65f3-24c7-4388-baec-2e87135dc908` |
| Azure Key Vault | Key Vault Secrets User (read-only), granted to both Container Apps' identity and the signed-in trainee — see ADR 0005 | `4633458b-17de-408a-b874-0445c86b69e6` |

The Foundry account exposes the same Azure-OpenAI-compatible inference
endpoint a bare `kind:'OpenAI'` account did (`properties.endpoint` + a
deployment name) — the role definition above is unchanged from the
pre-Foundry design, only the account's `kind` (`AIServices` instead of
`OpenAI`) and the addition of a child `projects` resource
(`allowProjectManagement: true`) changed. See the ADR for the exact API
version this was verified against.

`infra/resources.bicep` also grants the signed-in trainee's own account
(`principalId`, auto-populated by `azd` — see that file's parameter
description) **Search Index Data Contributor** + **Search Service
Contributor** on Search, **Cognitive Services OpenAI User** /
**Cognitive Services User** on Foundry/Content Safety, and (since ADR
0005) **Key Vault Secrets User** on the vault — this is what lets
`scripts/build_search_index.py` and `scripts/inject_stale_doc.py` run
from a trainee's own `az login`'d shell throughout both labs, and what
lets a trainee run `az keyvault secret show` to get `LLM_GATEWAY_API_KEY`
onto their own machine for local agent runs (Day1_Lab_Guide.md Part
0.7) — deliberately broader than the app's own runtime identity.

The Container App itself is deployed **once per environment**, by
`infra/resources.bicep`, with a public placeholder image. `azd deploy`
(part of `azd up` locally, and each `deploy-dev`/`deploy-staging`/
`deploy-production` GitHub Actions job in CI) is what overwrites it with
the real built image — infra owns the shell, the build/deploy step owns
the image, and re-running `azd provision` on its own never touches the
image that's currently running.

---

## 7. Security posture, consolidated

- **No stored secrets anywhere in the deployed path.** GitHub Actions
  authenticates to Azure via OIDC federation, wired up automatically by
  `azd pipeline config` — a short-lived token GitHub mints per workflow
  run, Azure trusts because of a one-time federated credential — not a
  stored `AZURE_CLIENT_SECRET`. The Container App authenticates to every
  downstream Cognitive Services resource via its own user-assigned
  managed identity, not an API key.
- **Least privilege, resource by resource.** The Container App's
  *runtime* identity gets **Search Index Data Reader** (query-only) on
  Search, never Contributor; index creation/rebuild
  (`scripts/build_search_index.py`) is a separate, human-run,
  `azd`/`az`-authenticated operation with broader rights, deliberately not
  something the running service can do to itself.
- **ACR admin access is disabled** (`adminUserEnabled: false`) — image
  pulls happen via the managed identity's AcrPull role.
- **Two independent deterministic boundaries, not one.**
  `tool_policy.agent_policy_layer()` decides what's *offered*;
  `tool_policy.enforce_tool_execution_boundary()` decides what's
  *actually executed*, re-validating `session_customer_id` against every
  tool argument independent of what any upstream layer (the injection
  heuristic, the real Content Safety check, or the model itself)
  decided. Neither probabilistic signal is ever the sole gate on an
  unsafe action — see `Day2_Lab_Guide.md` Part 6, the guardrail
  mini-lab, for this proven live against a real deployed endpoint.
- **Money never moves.** `request_customer_credit` has exactly two
  possible states in the entire codebase, `PENDING_APPROVAL` and
  `APPROVED_SIMULATED` — there is no `EXECUTED` state and no code path
  to a real financial transaction anywhere in this repo. Even
  `APPROVED_SIMULATED` requires a human to call
  `tool_policy.approve_credit_request()` explicitly; the agent has no
  path to call it itself.
- **What's honestly NOT hardened**, named rather than hidden: Foundry,
  Search, Content Safety, and — since ADR 0002 — the gateway Container
  App and Key Vault all deploy with `publicNetworkAccess: 'Enabled'` in
  every environment including `nimbus-production` — explicitly called
  out in `infra/resources.bicep`'s own comments as something to lock
  down (private endpoints, disabled public access) before this template
  takes real customer traffic. The gateway is protected only by its
  master key, not network isolation. There is no identity/JWT validation
  layer anywhere in this system — `customer_id`
  is trusted exactly as the caller supplies it, on every request, in
  every environment, including `nimbus-production`. This is a deliberate
  scoping decision, not an oversight: `nimbus-production` in this project
  is a **presentation-grade** environment — it looks and behaves like
  production but is not intended to take real customer traffic — so
  identity/APIM/WAF hardening (roadmap Section 4.7) is documented as a
  concrete next step rather than built. This is the single most
  important thing to name unprompted if this system, or a fork of it,
  is ever pointed at real traffic — see Section 8.

---

## 8. Known limitations, stated plainly

- Retrieval is keyword/BM25 only — no embeddings, no vector or hybrid
  search, in this template (a named scoping decision — see
  `app/retrieval/retrieval.py`'s module docstring).
- The evaluation datasets are small and hand-curated for teaching (see
  `Dataset_and_Evaluation_Guide.md`) — 26 total labeled cases across five
  files, not a production-scale evaluation corpus.
- Cost figures are estimates, not reconciled billing data — see Section
  6 of `Dataset_and_Evaluation_Guide.md` and `cost_tracking.py`'s module
  docstring.
- There is no identity/JWT validation layer. `customer_id` is a
  caller-supplied, trusted value on every request. This system must not
  be exposed to untrusted callers. Adding real Entra ID bearer-token
  validation is a natural, concrete next step — it needs a real Entra ID
  tenant this project's infra doesn't provision, and is out of scope
  while `nimbus-production` remains a presentation-grade environment
  rather than one taking real customer traffic (see Section 7).
- The Container App deploys a single active revision
  (`activeRevisionsMode: Single`) — no canary/staged traffic split
  yet. A production deployment taking real traffic should
  reintroduce one before promoting a new image to 100% of traffic — this
  is the platform maturity roadmap's Phase B, not yet built.
- The `code-scanning` CI stage (`scripts/mock_security_scan.py`) is a
  clearly-labeled placeholder — it performs no real static analysis,
  secrets detection, or CVE scanning. See that script's own docstring
  for the exact real-tool swap-in points (Semgrep, Gitleaks, Trivy,
  Checkov, or SonarQube) before relying on this pipeline as a real
  security gate.
- Human-in-the-loop escalation above the credit ceiling, and
  multi-region/DR, are both design-only callouts, not built.
