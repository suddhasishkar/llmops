"""
Synthetic mock tools for the Nimbus Telecom Support Copilot lab.

ALL data here is synthetic and generated for training purposes only.
`request_customer_credit` NEVER performs a financial transaction — it only
ever writes a mock approval-request record. There is no code path in this
file, or anywhere in this repository, that moves money.
"""
from __future__ import annotations

import uuid
import datetime
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Synthetic data stores (in-memory, reset per process — this is a lab, not a
# production datastore)
# ---------------------------------------------------------------------------

SYNTHETIC_CUSTOMERS = {
    "CUST-1001": {"name": "A. Rao", "plan": "Nimbus Unlimited 5G", "monthly_price": 45.00, "postcode": "SW1A"},
    "CUST-1002": {"name": "J. Chen", "plan": "Nimbus Basic 4G", "monthly_price": 22.00, "postcode": "E14"},
    "CUST-1003": {"name": "M. Osei", "plan": "Nimbus Family Bundle", "monthly_price": 68.00, "postcode": "M1"},
}

SYNTHETIC_BILLS = {
    "CUST-1001": {"period": "2026-07", "amount_due": 45.00, "status": "paid", "line_items": ["Plan fee: 45.00"]},
    "CUST-1002": {"period": "2026-07", "amount_due": 34.50, "status": "overdue", "line_items": ["Plan fee: 22.00", "Overage: 12.50"]},
    "CUST-1003": {"period": "2026-07", "amount_due": 68.00, "status": "paid", "line_items": ["Plan fee: 68.00"]},
}

SYNTHETIC_OUTAGES = {
    "SW1A": {"active": False},
    "E14": {"active": True, "eta": "2026-08-19T18:00:00Z", "cause": "planned maintenance"},
    "M1": {"active": False},
}

TICKETS: dict[str, dict] = {}
CREDIT_APPROVAL_REQUESTS: dict[str, dict] = {}

MAX_CREDIT_AMOUNT_USD = 50.00
MAX_CREDIT_REQUESTS_PER_CUSTOMER_PER_DAY = 3


class ToolAuthorizationError(Exception):
    """Raised when a tool call violates a deterministic policy-layer rule."""


class ToolArgumentError(Exception):
    """Raised when tool arguments fail schema/range validation."""


# ---------------------------------------------------------------------------
# Read-only tools
# ---------------------------------------------------------------------------

def get_customer_plan(customer_id: str, *, session_customer_id: str) -> dict:
    """Retrieve a synthetic customer's current plan.

    `session_customer_id` is the identity-layer-verified caller; this
    function refuses to serve any customer_id other than the authenticated
    session's own, regardless of what the model/agent passes.
    """
    if customer_id != session_customer_id:
        raise ToolAuthorizationError(
            f"Identity layer: cannot retrieve plan for {customer_id} "
            f"under session identity {session_customer_id}."
        )
    if customer_id not in SYNTHETIC_CUSTOMERS:
        raise ToolArgumentError(f"Unknown customer_id: {customer_id}")
    return {"customer_id": customer_id, **SYNTHETIC_CUSTOMERS[customer_id]}


def check_network_outage(postcode: str, *, simulate_slow: bool = False) -> dict:
    """Check for a synthetic network outage by postcode.

    `simulate_slow` is used only by the optional Day 1 slow-tool/retry lab
    to inject artificial latency; it has no effect on the returned data.
    """
    if simulate_slow:
        import time
        time.sleep(2.5)
    postcode = postcode.upper().strip()
    if postcode not in SYNTHETIC_OUTAGES:
        return {"postcode": postcode, "active": False, "note": "no data for postcode (treated as no known outage)"}
    return {"postcode": postcode, **SYNTHETIC_OUTAGES[postcode]}


def retrieve_latest_bill(customer_id: str, *, session_customer_id: str) -> dict:
    """Retrieve a synthetic customer's most recent bill."""
    if customer_id != session_customer_id:
        raise ToolAuthorizationError(
            f"Identity layer: cannot retrieve bill for {customer_id} "
            f"under session identity {session_customer_id}."
        )
    if customer_id not in SYNTHETIC_BILLS:
        raise ToolArgumentError(f"No bill on file for customer_id: {customer_id}")
    return {"customer_id": customer_id, **SYNTHETIC_BILLS[customer_id]}


# ---------------------------------------------------------------------------
# Controlled write tools
# ---------------------------------------------------------------------------

def create_support_ticket(customer_id: str, category: str, description: str, *, session_customer_id: str) -> dict:
    """Create a synthetic support ticket. No approval required; fully logged."""
    if customer_id != session_customer_id:
        raise ToolAuthorizationError(
            f"Identity layer: cannot create ticket for {customer_id} "
            f"under session identity {session_customer_id}."
        )
    allowed_categories = {"billing", "network", "plan_change", "cancellation", "general"}
    if category not in allowed_categories:
        raise ToolArgumentError(f"category must be one of {allowed_categories}, got {category!r}")
    ticket_id = f"TCK-{uuid.uuid4().hex[:8].upper()}"
    ticket = {
        "ticket_id": ticket_id,
        "customer_id": customer_id,
        "category": category,
        "description": description,
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "status": "open",
    }
    TICKETS[ticket_id] = ticket
    return ticket


def request_customer_credit(customer_id: str, amount: float, reason: str, *, session_customer_id: str) -> dict:
    """Create a MOCK credit-approval request. This function NEVER executes a
    transaction. It only ever writes a PENDING_APPROVAL record. A human must
    separately approve it (see app.agent.tool_policy.approve_credit_request)
    before the record can ever move to APPROVED — and even APPROVED is a
    simulated terminal state with no downstream financial effect.
    """
    if customer_id != session_customer_id:
        raise ToolAuthorizationError(
            f"Identity layer: cannot request credit for {customer_id} "
            f"under session identity {session_customer_id}."
        )
    if amount <= 0 or amount > MAX_CREDIT_AMOUNT_USD:
        raise ToolArgumentError(
            f"amount must be > 0 and <= {MAX_CREDIT_AMOUNT_USD} (per-transaction ceiling); got {amount}"
        )
    todays_requests = [
        r for r in CREDIT_APPROVAL_REQUESTS.values()
        if r["customer_id"] == customer_id
        and r["created_at"].startswith(datetime.datetime.utcnow().date().isoformat())
    ]
    if len(todays_requests) >= MAX_CREDIT_REQUESTS_PER_CUSTOMER_PER_DAY:
        raise ToolAuthorizationError(
            f"Transaction limit: customer {customer_id} has reached the daily "
            f"credit-request limit ({MAX_CREDIT_REQUESTS_PER_CUSTOMER_PER_DAY})."
        )
    request_id = f"CREDIT-REQ-{uuid.uuid4().hex[:8].upper()}"
    record = {
        "request_id": request_id,
        "customer_id": customer_id,
        "amount": round(amount, 2),
        "reason": reason,
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "state": "PENDING_APPROVAL",   # never auto-transitions to APPROVED
    }
    CREDIT_APPROVAL_REQUESTS[request_id] = record
    return record
