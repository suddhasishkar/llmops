"""Layer 1 structural checks for the Manager/Specialist multi-agent
package (app/agent/manager_agent.py, billing_agent.py, account_agent.py,
tool_schemas.py) and its five prompt files. Replaces the retired
test_support_agent_structure.py -- see
docs/adr/0001-foundry-and-multiagent.md.

These deliberately never call route()/decide() (get_client()) -- no
network, no Azure/Foundry credentials required, safe to run in CI exactly
like every other tests/unit file. What they DO check: TOOLS_SCHEMA
structurally matches app/agent/tools.py's real function signatures, each
specialist's tool-name subset is disjoint from what it shouldn't have
(AccountAgent has no monetary tool), all five prompt files load and parse
correctly, and -- most importantly -- that the billing candidate_broken
prompt really is missing the disambiguation clause the
baseline/candidate_fixed prompts have, which is the actual content-level
regression test for the "Repair a Blocked AI Release" exercise.
"""
from app.agent import tool_schemas
from app.agent.prompt_loader import load_prompt
from app.agent.billing_agent import PROMPT_VERSIONS as BILLING_PROMPT_VERSIONS
from tests.validate_tool_schemas import validate_call


def _schema_names(schema):
    return {t["function"]["name"] for t in schema}


def test_tools_schema_covers_all_five_real_tools():
    known = {
        "get_customer_plan", "check_network_outage", "retrieve_latest_bill",
        "create_support_ticket", "request_customer_credit",
    }
    assert _schema_names(tool_schemas.TOOLS_SCHEMA) == known


def test_tools_schema_arguments_pass_the_real_tool_contract():
    errs = validate_call("request_customer_credit", {
        "customer_id": "CUST-1001", "amount": 20.0, "reason": "x",
    })
    assert errs == []
    errs = validate_call("create_support_ticket", {
        "customer_id": "CUST-1002", "category": "general", "description": "x",
    })
    assert errs == []


def test_account_agent_has_no_monetary_tool():
    # The load-bearing safety property behind "route to account when
    # unsure": AccountAgent structurally cannot reach the credit tool.
    assert "request_customer_credit" not in tool_schemas.ACCOUNT_TOOL_NAMES


def test_billing_and_account_tool_sets_only_overlap_on_the_shared_escalation_tool():
    overlap = tool_schemas.BILLING_TOOL_NAMES & tool_schemas.ACCOUNT_TOOL_NAMES
    assert overlap == {"create_support_ticket"}


def test_manager_routing_schema_offers_exactly_two_functions():
    names = _schema_names(tool_schemas.MANAGER_ROUTING_SCHEMA)
    assert names == {"delegate_to_billing", "delegate_to_account"}


def test_manager_prompt_loads():
    text = load_prompt("manager")
    assert "[PERSONA]" in text
    assert "delegate_to_billing" in text or "delegate_to_account" in text


def test_account_prompt_loads():
    text = load_prompt("account")
    assert "[PERSONA]" in text
    assert "[BOUNDARIES - HARD RULES]" in text
    assert "[BEHAVIOR]" in text


def test_all_billing_prompt_versions_load():
    for version in sorted(BILLING_PROMPT_VERSIONS):
        text = load_prompt(f"billing_{version}")
        assert "[PERSONA]" in text
        assert "[BOUNDARIES - HARD RULES]" in text
        assert "[BEHAVIOR]" in text


def test_billing_candidate_broken_is_missing_the_disambiguation_clause():
    # The actual content-level regression test: candidate_broken must NOT
    # contain the disambiguation instruction; baseline and
    # candidate_fixed both must. If someone "fixes" the wrong file, or
    # the fault regresses to matching baseline, this test catches it --
    # independent of, and in addition to, the behavioral trajectory eval
    # that catches the same fault by actually running the agent.
    marker = "ambiguous"
    assert marker in load_prompt("billing_baseline").lower()
    assert marker in load_prompt("billing_candidate_fixed").lower()
    assert marker not in load_prompt("billing_candidate_broken").lower()
