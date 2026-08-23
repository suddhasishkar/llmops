"""
The Nimbus Account Specialist Agent.

Reached only after app/agent/manager_agent.py's `route()` returns
"account" -- including the default/safe path when the manager's routing
decision is missing or unclear. Scoped to exactly
`tool_schemas.ACCOUNT_TOOL_NAMES` (`get_customer_plan`,
`check_network_outage`, `create_support_ticket`) -- no monetary-capable
tool at all, by construction, which is exactly why "route here when
unsure" is a safe default.

Same "always a real model call, no stub" rule as every other model call
in this codebase -- see azure_openai_client.py's module docstring.
"""
from __future__ import annotations

import json
from typing import Optional

from app.agent.azure_openai_client import get_client, get_deployment_name
from app.agent.prompt_loader import load_prompt
from app.agent.tool_schemas import TOOLS_SCHEMA, ACCOUNT_TOOL_NAMES


def decide(user_message: str, customer_id: str, offered_tools: list[str]) -> Optional[dict]:
    """The one real model call for an account-routed turn. Restricted to
    the intersection of `tool_schemas.ACCOUNT_TOOL_NAMES` and whatever
    `offered_tools` survived the policy/content-safety layers above.

    Returns a tool-call decision only -- {"name": ..., "arguments": {...}}
    or None. Execution happens exactly once, in agent.py's tool_execution
    stage, at tool_policy.enforce_tool_execution_boundary().
    """
    system_instruction = load_prompt("account")

    scoped_names = ACCOUNT_TOOL_NAMES.intersection(offered_tools)
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
    args["customer_id"] = customer_id
    return {"name": call.function.name, "arguments": args}
