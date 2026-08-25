"""
LLM client factory for the Nimbus Support Agent.

Two functions only: a configured `OpenAI` client, and the model name to
call it with. Every agent role in this package (`manager_agent.py`,
`billing_agent.py`, `account_agent.py`) shares this one client factory --
one Foundry model deployment serves all three roles, different system
prompts, same underlying model, to keep cost down (see
docs/adr/0001-foundry-and-multiagent.md).

As of docs/adr/0002-llm-gateway-and-observability.md, this client talks
to the **LiteLLM gateway** (`infra/resources.bicep`'s `litellmGateway`
container app), never to Microsoft Foundry directly. The gateway is what
actually authenticates to Foundry, via the same user-assigned managed
identity every other Azure client in this repo uses
(`AZURE_CREDENTIAL=ManagedIdentityCredential` on the gateway container --
see `gateway/litellm_config.yaml`). This process authenticates to the
gateway itself with a shared key (`LLM_GATEWAY_API_KEY`), since the
gateway's OpenAI-compatible proxy surface expects a bearer key the way
any OpenAI-compatible endpoint does -- that key is a platform-internal,
deterministically-generated secret (see `resources.bicep`'s
`litellmMasterKey` parameter), never a customer-facing credential and
never a real Azure/cloud-provider credential. It is the one deliberate,
documented exception to this repo's "managed identity only, no keys"
rule, scoped narrowly to the api-container-to-gateway hop.

There is no local/offline fallback anywhere in this module or in
anything that calls it. That is a deliberate rebuild decision, not an
oversight: every earlier version of this program defaulted to a free,
deterministic offline stub and treated the real Azure OpenAI call as an
opt-in — which meant "cloud" was never really the default anywhere, no
matter how many env vars pointed at it. This rebuild removes the
stub entirely, so there is nothing left to silently fall back to:
`manager_agent.route()` and each specialist's `decide()` always make a
real network call, every lab exercise and every CI job that exercises
them needs a live gateway (and, behind it, live Foundry) reachable, and
that trade-off is stated here rather than hidden behind a default. See
`Agent_End_to_End_Architecture.md` Section 4 for the full reasoning.
"""
from __future__ import annotations

import os


def get_client():
    """Return a configured openai.OpenAI client, pointed at the LiteLLM
    gateway's OpenAI-compatible endpoint -- not at Foundry directly."""
    try:
        from openai import OpenAI
    except ImportError as e:
        raise ImportError(
            "The 'openai' package is required. Install it with: "
            "pip install openai"
        ) from e

    endpoint = os.getenv("LLM_GATEWAY_ENDPOINT")
    if not endpoint:
        raise RuntimeError(
            "LLM_GATEWAY_ENDPOINT is not set. `azd up` (see Day1_Lab_Guide.md "
            "Part 1) writes this into .azure/<env>/.env automatically -- it's "
            "the LiteLLM gateway container app's URL (infra/resources.bicep's "
            "litellmGateway resource), not Foundry's own endpoint. Run "
            "`azd env get-values` and export it, or `source <(azd env get-values)`."
        )

    api_key = os.getenv("LLM_GATEWAY_API_KEY")
    if not api_key:
        raise RuntimeError(
            "LLM_GATEWAY_API_KEY is not set. This is the gateway's shared "
            "master key (infra/resources.bicep's litellmMasterKey parameter), "
            "not an Azure OpenAI/Foundry key -- see "
            "docs/adr/0002-llm-gateway-and-observability.md. `azd up` injects "
            "it automatically into the deployed api container app; for local "
            "runs, `azd env get-values` and export it the same way."
        )

    # Explicit timeout + retry budget -- without these, the openai SDK's own
    # defaults apply (openai._constants: 600s read timeout, max_retries=2),
    # which means a single stuck/cold-starting gateway call can silently
    # occupy up to ~30 minutes (600s x up to 3 attempts with backoff) before
    # ever raising, with zero output in between. That is exactly what a CI
    # step "stuck since 11min" looks like: it isn't hung forever, it just
    # hasn't finished retrying yet. 60s/attempt, 1 retry keeps the worst case
    # under ~3 minutes and turns a silent CI hang into a fast, visible
    # failure that names the real problem (gateway unreachable/unhealthy)
    # instead of masking it behind a long, unexplained wait.
    return OpenAI(base_url=f"{endpoint.rstrip('/')}/v1", api_key=api_key, timeout=60.0, max_retries=1)


def get_deployment_name() -> str:
    """The model name to pass to `client.chat.completions.create(model=...)`.

    This is the LiteLLM `model_name` alias declared in
    `gateway/litellm_config.yaml` (`nimbus-copilot-chat`), which happens
    to match the underlying Foundry deployment name -- the env var name
    is unchanged from before the gateway migration since the value's
    meaning to callers of this function (the model alias to request) is
    unchanged.
    """
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    if not deployment:
        raise RuntimeError(
            "AZURE_OPENAI_DEPLOYMENT is not set. `azd up` writes this "
            "automatically -- see Day1_Lab_Guide.md Part 0."
        )
    return deployment