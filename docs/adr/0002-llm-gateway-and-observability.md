# ADR 0002 — Add an LLM gateway (LiteLLM Proxy) and LLM observability (Langfuse)

**Status:** Accepted, in progress. **Date:** 2026-08-20.

## Context

Following ADR 0001's Foundry + multi-agent migration, the explicit ask
was to add an LLM gateway and LLM-specific monitoring/tracing "so no
stone is left unturned" on the LLMOps side, with one hard constraint:
any new tool must be **MIT-licensed**. This has to be reconciled with
the standing "minimum budget across three environments" decision from
ADR 0001 — the two constraints pull in different directions for some
options, and that tension is named explicitly below rather than
papered over.

## Research findings (verified via web search + primary-source docs, cited)

**Licenses (confirmed, not assumed):**

| Tool | Confirmed license | Full feature set under that license? |
|---|---|---|
| **LiteLLM** (proxy/gateway) | MIT | Yes for the proxy itself — only the separate `enterprise/` directory (SSO/SAML, some advanced features) is under a different license |
| **Langfuse** (core) | MIT | Yes for tracing/observability/prompt management — only `ee/` (SSO/SCIM, some RBAC) is under a separate Enterprise license. Now owned by ClickHouse, Inc.; license text is unchanged |
| MLflow | Apache-2.0 | N/A — not MIT, ruled out on the license constraint alone |
| Arize Phoenix | Elastic License 2.0 | N/A — not MIT (and not OSI-approved open source), ruled out |
| Helicone | Apache-2.0 | N/A — not MIT, ruled out |

Sources: [LiteLLM LICENSE](https://raw.githubusercontent.com/BerriAI/litellm/main/LICENSE), [Langfuse LICENSE](https://raw.githubusercontent.com/langfuse/langfuse/main/LICENSE), [Langfuse `ee/LICENSE`](https://raw.githubusercontent.com/langfuse/langfuse/main/ee/LICENSE), [MLflow LICENSE.txt](https://raw.githubusercontent.com/mlflow/mlflow/master/LICENSE.txt), [Arize Phoenix LICENSE](https://raw.githubusercontent.com/Arize-ai/phoenix/main/LICENSE), [Helicone LICENSE](https://raw.githubusercontent.com/Helicone/helicone/main/LICENSE).

**The named tension:** Langfuse's core is genuinely MIT, but **self-hosting**
it needs four backing services (Postgres, ClickHouse, Redis, S3-compatible
storage) — real, ongoing cost across three environments, directly
conflicting with the minimum-budget decision. [Langfuse self-hosting
docs](https://langfuse.com/self-hosting) confirm this footprint.
Langfuse also offers a **free Langfuse Cloud Hobby plan** (no card, 50k
units/month, 30-day retention) — [Langfuse pricing](https://langfuse.com/pricing)
— which keeps the MIT-licensed software story intact (you're still
using Langfuse, not a fork) while adding **zero new infrastructure**.
Given you chose "Langfuse Cloud free tier" when asked, that's the
default this ADR implements.

**LiteLLM Proxy config, verified against current docs (not guessed):**
Azure AD/managed-identity auth is enabled via
`litellm_settings.enable_azure_ad_token_refresh: true` plus
`AZURE_CREDENTIAL`/`AZURE_CLIENT_ID` environment variables — no static
API key anywhere in `gateway/litellm_config.yaml`, consistent with this
repo's existing managed-identity-only pattern. [LiteLLM Azure AD PR
#8468](https://github.com/BerriAI/litellm/pull/8468). Master-key auth
(`general_settings.master_key: os.environ/LITELLM_MASTER_KEY`) and the
Langfuse callback (`litellm_settings.success_callback: ["langfuse"]`,
reading `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`/`LANGFUSE_HOST`) are
both confirmed directly against [docs.litellm.ai/docs/proxy/master_key_rotations](https://docs.litellm.ai/docs/proxy/master_key_rotations)
and [docs.litellm.ai/docs/observability/langfuse_integration](https://docs.litellm.ai/docs/observability/langfuse_integration).
A **stateless** proxy (no `DATABASE_URL`/Postgres) runs fine for routing,
master-key auth, and callbacks — it only loses virtual per-key
budgets/spend tracking, which this project doesn't need for a shared
demo key. [docs.litellm.ai/docs/proxy/docker_quick_start](https://docs.litellm.ai/docs/proxy/docker_quick_start).

**One item not fully corroborated against primary docs:** the exact
behavior of the Langfuse `success_callback` when
`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` are left empty (expected:
harmless logged errors per request, not a failed request) — flagged in
`gateway/litellm_config.yaml`'s comments rather than asserted as fact.

**Azure-native alternative considered and not chosen for this pass:**
Azure API Management's GenAI gateway capabilities (token-limit policies,
load balancing, `llm-emit-token-metric`) are available on the
Consumption tier (first 1M calls/month free) and could replace a
third-party gateway for basic routing/governance. [APIM GenAI gateway
docs](https://learn.microsoft.com/en-us/azure/api-management/genai-gateway-capabilities).
Not adopted here because (a) it doesn't provide a dedicated LLM
trace/eval UI the way Langfuse does, and (b) the newer "AI Gateway tier"
with richer built-in telemetry is still in public preview with no
defined pricing as of this research pass — not something to commit to
in a client-facing asset yet. Worth revisiting in a future ADR once that
tier is GA.

## Decision

1. **Add a LiteLLM Proxy gateway** (`gateway/`, deployed as a new
   `litellmGateway` Container App in `infra/resources.bicep`) between
   every agent role and Foundry. `app/agent/azure_openai_client.py` now
   calls the gateway's OpenAI-compatible endpoint
   (`openai.OpenAI(base_url=..., api_key=...)`) instead of Foundry's
   Azure-specific endpoint directly (`openai.AzureOpenAI`) — this is a
   real code change, not just infra. The gateway authenticates to
   Foundry via the same managed identity every other service in this
   repo uses; nothing changes about how Foundry itself is secured.
2. **Run it stateless** (no Postgres) with one shared, deterministically-generated
   master key (`litellmMasterKey` in `resources.bicep`), per your
   explicit choice — appropriate for a presentation-grade demo, not a
   real multi-tenant deployment. Documented as a scope boundary, not
   hidden.
3. **Deploy the gateway with external ingress**, protected only by the
   master key, matching the same "presentation-grade, not
   network-isolated" caveat this repo already states for Foundry and
   Content Safety (`publicNetworkAccess: 'Enabled'`) — chosen over
   internal-only ingress specifically so local/classroom workflows that
   already talk to cloud services directly keep working unchanged.
4. **Wire Langfuse in at the gateway layer**, not in agent code — one
   `success_callback: ["langfuse"]` line in `gateway/litellm_config.yaml`
   captures traces for all three agent roles (manager, billing, account)
   automatically, with zero changes to `manager_agent.py`/`billing_agent.py`/
   `account_agent.py`. Default to the free Langfuse Cloud Hobby tier
   (`langfuseHost` defaults to `https://cloud.langfuse.com`); leaving
   `langfusePublicKey`/`langfuseSecretKey` empty runs the gateway with
   tracing off, no error.
5. **Deployed to all three environments** (dev/staging/production), per
   your choice — one gateway image, same `azd up` flow, no new
   per-environment toggle.

## Consequences

- New files: `gateway/litellm_config.yaml`, `gateway/Dockerfile`.
- `azure.yaml` gains a second service (`gateway`) alongside `api`.
- `infra/resources.bicep` gains the `litellmGateway` Container App,
  `litellmMasterKey`/`langfusePublicKey`/`langfuseSecretKey`/`langfuseHost`
  parameters, and a `llmGatewayEndpoint` output. The existing `api`
  Container App's `AZURE_OPENAI_ENDPOINT` env var is replaced by
  `LLM_GATEWAY_ENDPOINT` + `LLM_GATEWAY_API_KEY` (the latter a Container
  App secret, not a plain env value). `infra/main.bicep` and
  `infra/main.parameters.json` gain the Langfuse pass-through params.
- `app/agent/azure_openai_client.py` is rewritten: `get_client()` now
  returns `openai.OpenAI` against the gateway, not
  `openai.AzureOpenAI` against Foundry. `manager_agent.py`,
  `billing_agent.py`, and `account_agent.py` are unaffected — they only
  call `get_client()`/`get_deployment_name()`, never the client class
  directly.
- **Named, not yet resolved:** local/offline scripts that call
  `get_client()` outside the deployed api container (ad hoc debugging,
  future local integration tests) need `LLM_GATEWAY_API_KEY` exported by
  hand — it is currently only injected automatically into the deployed
  Container App, not surfaced through `azd env get-values` the way
  Foundry's own endpoint is. Tracked as a follow-up, not blocking.
- **Cascading doc updates still required** (tracked, not done in this
  pass): `README.md`'s repository-structure and `azd up` sections,
  `Agent_End_to_End_Architecture.md`, `SYSTEM_CARD.md`,
  `Day1_Lab_Guide.md`/`Day2_Lab_Guide.md` wherever they describe the
  agent calling "Foundry" directly, and
  `Platform_Maturity_Roadmap.md` Section 4 (LLMOps observability row).
- **Cost note:** the gateway container follows the same
  `containerAppMinReplicas` scale-to-zero pattern as the api container —
  no new idle cost beyond one more Container App revision. Langfuse
  Cloud's free Hobby tier adds zero infrastructure cost; its 50k
  units/month and 30-day retention limits are acceptable for a
  presentation-grade demo and are the honest trade-off of choosing the
  free tier over self-hosting.
