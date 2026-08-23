"""
Persistent audit trail for the Nimbus Support Copilot -- Phase C
(docs/adr/0004-llmops-agentops-rigor.md).

Every tool-execution decision (attempted, allowed, denied, executed,
credit requested, credit approved) is written as one row to Azure Table
Storage, via the same managed-identity pattern every other Azure client
in this repo uses -- no connection string, no key.

THIS IS DELIBERATELY SEPARATE FROM app/agent/tools.py's in-memory
synthetic stores (TICKETS, CREDIT_APPROVAL_REQUESTS). Those remain
in-memory mock business data, unchanged -- they still reset on
container restart/scale-to-zero, which is a real, named limitation (see
the ADR's Consequences section), not silently fixed here. This module
adds the GOVERNANCE record of what happened and when, which is what an
audit trail is actually for -- it does not attempt to make the mock
business data itself durable, which would be a larger, different change
to tools.py's synthetic-data design.

Fail-safe by design: a failure to write an audit row NEVER blocks or
raises out to the caller. An audit system that can take down the
customer-facing chat flow because a storage write hiccuped is worse than
one that occasionally misses a row and logs why -- this mirrors the
same judgment call already made throughout this repo (e.g. Langfuse
callbacks in gateway/litellm_config.yaml are similarly fire-and-forget).
"""
from __future__ import annotations

import datetime
import logging
import os
import uuid

logger = logging.getLogger("nimbus.audit_log")

_TABLE_NAME = "auditlog"
_table_client = None
_init_attempted = False


def _get_table_client():
    """Lazily construct and cache a TableClient. Returns None (never
    raises) if AZURE_STORAGE_TABLE_ENDPOINT isn't set or the SDK/auth
    isn't available -- callers must treat a None client as "audit
    logging is unavailable right now," not as a fatal error."""
    global _table_client, _init_attempted
    if _table_client is not None or _init_attempted:
        return _table_client
    _init_attempted = True

    endpoint = os.getenv("AZURE_STORAGE_TABLE_ENDPOINT")
    if not endpoint:
        logger.warning("AZURE_STORAGE_TABLE_ENDPOINT not set -- audit log writes will be skipped, not retried.")
        return None
    try:
        from azure.data.tables import TableServiceClient
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential(managed_identity_client_id=os.getenv("AZURE_CLIENT_ID"))
        service_client = TableServiceClient(endpoint=endpoint, credential=credential)
        _table_client = service_client.create_table_if_not_exists(_TABLE_NAME)
    except Exception:
        logger.exception("Could not initialize the audit-log TableClient -- audit writes will be skipped.")
        _table_client = None
    return _table_client


def record_event(
    *,
    event_type: str,
    customer_id: str,
    agent_role: str,
    detail: dict,
) -> None:
    """Write one audit row. Never raises.

    event_type: one of "tool_call_attempted", "tool_call_allowed",
    "tool_call_denied", "tool_call_executed", "credit_requested",
    "credit_approved", "content_safety_blocked", "prompt_injection_flagged".
    agent_role: "manager" | "billing" | "account" | "api".
    detail: small, JSON-serializable dict of event-specific fields
    (tool_name, arguments summary, reason, request_id, approver, etc.)
    -- kept intentionally small since Table Storage entity properties
    have size limits; this is an audit trail, not a full-payload log
    store (Application Insights, already wired in app/api/main.py,
    is where full request/response payloads belong).
    """
    client = _get_table_client()
    if client is None:
        return

    now = datetime.datetime.utcnow()
    entity = {
        "PartitionKey": customer_id or "unknown",
        "RowKey": f"{now.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}",
        "event_type": event_type,
        "agent_role": agent_role,
        "timestamp": now.isoformat() + "Z",
    }
    for key, value in detail.items():
        # Table Storage entity properties must be primitive types --
        # stringify anything else rather than dropping it silently.
        entity[key] = value if isinstance(value, (str, int, float, bool)) else str(value)

    try:
        client.upsert_entity(entity)
    except Exception:
        logger.exception("Audit log write failed for event_type=%s customer_id=%s -- continuing without it.", event_type, customer_id)
