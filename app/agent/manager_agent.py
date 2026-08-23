"""
The Nimbus Manager Agent -- routing only, no tool access.

Second half of the Manager/Specialist split reintroduced in
docs/adr/0001-foundry-and-multiagent.md. `route()` makes one real Foundry
(Azure-OpenAI-compatible) function-calling call, restricted to exactly
two functions (`delegate_to_billing`, `delegate_to_account`, see
app/agent/tool_schemas.py's `MANAGER_ROUTING_SCHEMA`). The manager never
sees the real tool schemas (`retrieve_latest_bill`,
`request_customer_credit`, etc.) at all -- it structurally cannot call
one, regardless of what the customer's message asks for.

`route()` defaults to "account" (the specialist with no monetary-capable
tool) whenever the model's decision is missing, malformed, or anything
other than exactly one of the two known routing functions -- a
deliberate safety default, not just an error fallback. This is the same
"fail toward the least-capable path" principle
`tool_policy.agent_policy_layer()` already applies to the credit tool.
"""
from __future__ import annotations

from app.agent.azure_openai_client import get_client, get_deployment_name
from app.agent.prompt_loader import load_prompt
from app.agent.tool_schemas import MANAGER_ROUTING_SCHEMA

VALID_ROUTES = {"delegate_to_billing": "billing", "delegate_to_account": "account"}
DEFAULT_ROUTE = "account"


def route(user_message: str) -> str:
    """Returns "billing" or "account". Always one real model call; there
    is no local/offline fallback, matching every other model call in
    this codebase (see azure_openai_client.py's module docstring).
    """
    system_instruction = load_prompt("manager")

    client = get_client()
    response = client.chat.completions.create(
        model=get_deployment_name(),
        messages=[
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_message},
        ],
        tools=MANAGER_ROUTING_SCHEMA,
        tool_choice="required",
    )
    message = response.choices[0].message
    if not message.tool_calls:
        return DEFAULT_ROUTE
    function_name = message.tool_calls[0].function.name
    return VALID_ROUTES.get(function_name, DEFAULT_ROUTE)
