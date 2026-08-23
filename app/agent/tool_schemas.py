"""
Shared tool schema definitions for the Manager/Specialist multi-agent
architecture (see docs/adr/0001-foundry-and-multiagent.md).

Single source of truth for the five Nimbus support tool definitions, so
`billing_agent.py` and `account_agent.py` never duplicate a schema and
drift apart. Each specialist imports the master `TOOLS_SCHEMA` list and
filters it against its own `*_TOOL_NAMES` set at call time -- the same
"offer only what survived the policy layer, intersected with what this
caller is even allowed to see" pattern the single-agent build used, now
applied per specialist rather than globally.

`create_support_ticket` is deliberately shared by both specialists --
either one can escalate to a human, and BillingAgent uses it for the
general/ambiguous billing complaint path that the Day 1 lab's
disambiguation fix is about.
"""
from __future__ import annotations

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_customer_plan",
            "description": "Look up the customer's current Nimbus plan.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_network_outage",
            "description": "Check for a known network outage at the customer's postcode.",
            "parameters": {
                "type": "object",
                "properties": {"postcode": {"type": "string", "description": "UK postcode, e.g. E14"}},
                "required": ["postcode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retrieve_latest_bill",
            "description": "Fetch the customer's most recent bill.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_support_ticket",
            "description": "Escalate to a human when no other tool clearly applies, or for a general/ambiguous billing complaint.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": ["billing", "network", "plan_change", "cancellation", "general"]},
                    "description": {"type": "string"},
                },
                "required": ["category", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_customer_credit",
            "description": "Request a monetary credit for an EXPLICIT, specific customer request. Creates a pending-approval record only — never executes a payment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number", "description": "USD, must be > 0 and <= 50.00"},
                    "reason": {"type": "string"},
                },
                "required": ["amount", "reason"],
            },
        },
    },
]

# Each specialist only ever sees its own subset -- this is a second,
# static narrowing on top of the dynamic per-turn narrowing already
# applied by tool_policy.agent_policy_layer() + content_safety.check_content().
# A specialist literally cannot be handed a tool schema outside this set,
# regardless of what the manager or the model attempts.
BILLING_TOOL_NAMES = {"retrieve_latest_bill", "create_support_ticket", "request_customer_credit"}
ACCOUNT_TOOL_NAMES = {"get_customer_plan", "check_network_outage", "create_support_ticket"}

# The manager never sees TOOLS_SCHEMA at all -- only these two routing
# functions. It cannot call a real tool under any circumstance; it can
# only pick which specialist gets to see (an intersected subset of)
# TOOLS_SCHEMA next.
MANAGER_ROUTING_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "delegate_to_billing",
            "description": "Route this turn to the Billing specialist -- billing, invoice, payment, refund, or credit/compensation requests.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_to_account",
            "description": "Route this turn to the Account specialist -- plan questions, connectivity/outage reports, and anything general or unclear. This is the default/safe choice when routing is ambiguous.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]
