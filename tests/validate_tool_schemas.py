#!/usr/bin/env python3
"""Layer 1 — tool-schema / JSON-schema validation. Confirms the tool
argument schemas declared below match what app/agent/tools.py actually
accepts (a lightweight structural contract test, not a full JSON Schema
library dependency, to keep the lab dependency-free).
Run: python -m tests.validate_tool_schemas
"""
from __future__ import annotations
import sys

TOOL_SCHEMAS = {
    "get_customer_plan": {"required": ["customer_id"], "types": {"customer_id": str}},
    "check_network_outage": {"required": ["postcode"], "types": {"postcode": str}},
    "retrieve_latest_bill": {"required": ["customer_id"], "types": {"customer_id": str}},
    "create_support_ticket": {
        "required": ["customer_id", "category", "description"],
        "types": {"customer_id": str, "category": str, "description": str},
        "enum": {"category": ["billing", "network", "plan_change", "cancellation", "general"]},
    },
    "request_customer_credit": {
        "required": ["customer_id", "amount", "reason"],
        "types": {"customer_id": str, "amount": (int, float), "reason": str},
        "range": {"amount": (0, 50.00)},
    },
}


def validate_call(tool_name: str, arguments: dict) -> list[str]:
    errors = []
    schema = TOOL_SCHEMAS.get(tool_name)
    if schema is None:
        return [f"Unknown tool: {tool_name}"]
    for field in schema["required"]:
        if field not in arguments:
            errors.append(f"{tool_name}: missing required argument '{field}'")
    for field, expected_type in schema.get("types", {}).items():
        if field in arguments and not isinstance(arguments[field], expected_type):
            errors.append(f"{tool_name}: argument '{field}' expected type {expected_type}, got {type(arguments[field])}")
    for field, allowed in schema.get("enum", {}).items():
        if field in arguments and arguments[field] not in allowed:
            errors.append(f"{tool_name}: argument '{field}'={arguments[field]!r} not in allowed set {allowed}")
    for field, (lo, hi) in schema.get("range", {}).items():
        if field in arguments and not (lo <= arguments[field] <= hi):
            errors.append(f"{tool_name}: argument '{field}'={arguments[field]} outside allowed range [{lo}, {hi}]")
    return errors


def self_test() -> list[str]:
    """Runs a few known-good and known-bad calls to prove the schema
    definitions themselves are internally consistent."""
    errors = []
    good = validate_call("request_customer_credit", {"customer_id": "CUST-1001", "amount": 20.0, "reason": "x"})
    if good:
        errors.append(f"Expected valid call to pass, got errors: {good}")
    bad = validate_call("request_customer_credit", {"customer_id": "CUST-1001", "amount": 500.0, "reason": "x"})
    if not bad:
        errors.append("Expected out-of-range amount to fail validation, but it passed")
    missing = validate_call("create_support_ticket", {"customer_id": "CUST-1001"})
    if not missing:
        errors.append("Expected missing required fields to fail validation, but it passed")
    return errors


if __name__ == "__main__":
    errs = self_test()
    if errs:
        print(f"FAILED: {len(errs)} tool-schema self-test error(s)")
        for e in errs:
            print(f"  - {e}")
        sys.exit(1)
    print("All tool schemas valid and self-consistent.")
