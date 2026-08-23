"""Layer 1 unit tests for app/agent/audit_log.py -- the persistent audit
trail added in docs/adr/0004-llmops-agentops-rigor.md. These deliberately
never reach a real Table Storage account (no AZURE_STORAGE_TABLE_ENDPOINT
in this test process) -- what they lock in is the FAIL-SAFE CONTRACT:
record_event() must never raise, with or without a configured endpoint,
because an audit-log hiccup must never take down the customer-facing
chat flow. Run: pytest tests/unit
"""
from app.agent import audit_log


def test_record_event_does_not_raise_without_configured_endpoint(monkeypatch):
    monkeypatch.delenv("AZURE_STORAGE_TABLE_ENDPOINT", raising=False)
    audit_log._table_client = None
    audit_log._init_attempted = False
    # Must not raise -- this is the whole point of the fail-safe design.
    audit_log.record_event(
        event_type="tool_call_attempted",
        customer_id="CUST-1001",
        agent_role="billing",
        detail={"tool_name": "retrieve_latest_bill"},
    )


def test_record_event_stringifies_non_primitive_detail_values(monkeypatch):
    # Table Storage entities only accept primitive property types --
    # record_event must coerce anything else to a string rather than
    # crash or silently drop the field. Exercised through a fake table
    # client so this test stays offline/deterministic.
    written = {}

    class FakeTableClient:
        def upsert_entity(self, entity):
            written.update(entity)

    monkeypatch.setattr(audit_log, "_get_table_client", lambda: FakeTableClient())
    audit_log.record_event(
        event_type="tool_call_executed",
        customer_id="CUST-1001",
        agent_role="account",
        detail={"tags": ["a", "b"], "amount": 12.5, "ok": True},
    )
    assert written["tags"] == "['a', 'b']"
    assert written["amount"] == 12.5
    assert written["ok"] is True
    assert written["PartitionKey"] == "CUST-1001"
    assert "RowKey" in written
