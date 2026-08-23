"""
Per-turn token/cost estimation for the Nimbus Support Copilot.

Line 229 of 03_Day2_Governance_Monitoring_LLMOps_AgentOps.md lists
"tokens · cost" among the things a real system monitors online -- this
module is what makes that concrete instead of a bullet point nobody ever
implemented. See planning/Interview_Readiness_Enrichment_Plan.md item A4.

IMPORTANT HONESTY NOTE, worth repeating to trainees: the numbers this
module produces are ESTIMATES, not billed usage. A real integration would
read the `usage.prompt_tokens` / `usage.completion_tokens` fields the
Azure OpenAI SDK actually returns on every response object (see
azure_openai_client.py -- decide_tool_call() currently discards that
object after extracting the tool-call decision; wiring the real usage
through is a small, concrete next step worth naming out loud rather than
silently estimating forever). Until that's wired through, this module
estimates tokens with the same rough heuristic (~4 characters per token
for English text) OpenAI's own documentation uses for back-of-envelope
sizing -- good enough to catch a cost regression trend, not good enough
to reconcile against an invoice.

PRICING is a snapshot, not a live source of truth. Prices change; verify
current rates at https://azure.microsoft.com/pricing/details/cognitive-services/openai-service/
before using this for real budget decisions.
"""
from __future__ import annotations

from dataclasses import dataclass

# USD per 1,000 tokens, gpt-4o-mini, as of this program's last pricing
# check -- see module docstring. Deliberately a plain dict, not a live
# API call, so this stays free and offline like every other lab default.
PRICING_USD_PER_1K_TOKENS = {
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
}
DEFAULT_MODEL = "gpt-4o-mini"


@dataclass
class CostEstimate:
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost_usd: float
    model: str
    is_estimate: bool = True


def estimate_tokens(text: str) -> int:
    """~4 characters per token, English text -- OpenAI's own documented
    rule of thumb for rough sizing. Not a real tokenizer; a real
    integration would use `tiktoken` against the specific model, or the
    real `usage` object from the API response (see module docstring).
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def estimate_turn_cost(input_text: str, output_text: str, *, model: str = DEFAULT_MODEL) -> CostEstimate:
    pricing = PRICING_USD_PER_1K_TOKENS.get(model, PRICING_USD_PER_1K_TOKENS[DEFAULT_MODEL])
    in_tokens = estimate_tokens(input_text)
    out_tokens = estimate_tokens(output_text)
    cost = (in_tokens / 1000) * pricing["input"] + (out_tokens / 1000) * pricing["output"]
    return CostEstimate(
        estimated_input_tokens=in_tokens,
        estimated_output_tokens=out_tokens,
        estimated_cost_usd=round(cost, 8),
        model=model,
    )
