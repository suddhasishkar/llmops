"""
The Nimbus Billing Specialist Agent.

Reached only after app/agent/manager_agent.py's `route()` returns
"billing". Scoped to exactly `tool_schemas.BILLING_TOOL_NAMES`
(`retrieve_latest_bill`, `create_support_ticket`,
`request_customer_credit`) -- this module cannot construct a tool schema
outside that set even if asked to, regardless of what `offered_tools`
(computed one layer up by tool_policy.agent_policy_layer() +
content_safety.check_content()) contains.

This is where the Day 1 lab's disambiguation fault lives:
`system_prompt_billing_baseline.md` (fixed) vs.
`system_prompt_billing_candidate_broken.md` (the regression under
review) vs. `system_prompt_billing_candidate_fixed.md` (the proposed
fix, restoring the same disambiguation instruction with a shorter
persona). See docs/adr/0001-foundry-and-multiagent.md.

Same "always a real model call, no stub" rule as every other model call
in this codebase -- see azure_openai_client.py's module docstring.
"""
from __future__ import annotations

import json
from typing import Optional

from app.agent.azure_openai_client import get_client, get_deployment_name
from app.agent.prompt_loader import load_prompt
from app.agent.tool_schemas import TOOLS_SCHEMA, BILLING_TOOL_NAMES

PROMPT_VERSIONS = {"baseline", "candidate_broken", "candidate_fixed"}


def decide(user_message: str, customer_id: str, offered_tools: list[str], *, prompt_version: str = "baseline") -> Optional[dict]:
    """The one real model call for a billing-routed turn. Restricted to
    the intersection of `tool_schemas.BILLING_TOOL_NAMES` and whatever
    `offered_tools` survived the policy/content-safety layers above --
    neither set alone is trusted; both narrowings apply together.

    Returns a tool-call decision only -- {"name": ..., "arguments": {...}}
    or None. Execution happens exactly once, in agent.py's tool_execution
    stage, at tool_policy.enforce_tool_execution_boundary().
    """
    if prompt_version not in PROMPT_VERSIONS:
        raise ValueError(f"Unknown billing prompt version: {prompt_version} (expected one of {sorted(PROMPT_VERSIONS)})")
    system_instruction = load_prompt(f"billing_{prompt_version}")

    scoped_names = BILLING_TOOL_NAMES.intersection(offered_tools)
    tools = [t for t in TOOLS_SCHEMA if t["function"]["name"] in scoped_names]

    client = get_client()
    response = client.chat.completions.create(
        model=get_deployment_name(),
        messages=[
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_message},
        ],
        tools=tools,
        tool_choice="auto" if tools else "none",
    )
    message = response.choices[0].message
    if not message.tool_calls:
        return None
    call = message.tool_calls[0]
    args = json.loads(call.function.arguments or "{}")
    # customer_id is always the session's own value, never something the
    # model is asked to supply or the customer's message can override --
    # tools.py's functions additionally re-check this at execution time.
    args["customer_id"] = customer_id
    return {"name": call.function.name, "arguments": args}
