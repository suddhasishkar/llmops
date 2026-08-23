"""Layer 1 unit tests for app/agent/tools.py. Run: pytest tests/unit"""
import pytest
from app.agent import tools


def test_get_customer_plan_ok():
    result = tools.get_customer_plan("CUST-1001", session_customer_id="CUST-1001")
    assert result["customer_id"] == "CUST-1001"
    assert "plan" in result


def test_get_customer_plan_blocks_cross_account():
    with pytest.raises(tools.ToolAuthorizationError):
        tools.get_customer_plan("CUST-1002", session_customer_id="CUST-1001")


def test_request_customer_credit_creates_pending_only():
    record = tools.request_customer_credit("CUST-1001", 20.0, "test", session_customer_id="CUST-1001")
    assert record["state"] == "PENDING_APPROVAL"
    assert record["amount"] == 20.0


def test_request_customer_credit_rejects_over_ceiling():
    with pytest.raises(tools.ToolArgumentError):
        tools.request_customer_credit("CUST-1001", 500.0, "test", session_customer_id="CUST-1001")


def test_create_support_ticket_rejects_bad_category():
    with pytest.raises(tools.ToolArgumentError):
        tools.create_support_ticket("CUST-1001", "not-a-real-category", "desc", session_customer_id="CUST-1001")
