#!/usr/bin/env python3
"""Layer 1-adjacent sanity check: confirms every eval dataset is present,
parses as JSONL, and has the fields its corresponding runner expects.
Run: python -m eval.validate_datasets
"""
from __future__ import annotations
import json, sys
from pathlib import Path

DATASET_DIR = Path(__file__).parent / "datasets"

REQUIRED_FIELDS = {
    "rag_eval.jsonl": ["id", "query"],
    "tool_trajectory_eval.jsonl": ["id", "input", "expected_trajectory", "expected_final_action"],
    "safety_regression.jsonl": ["id", "input", "expected_behavior"],
    "prompt_injection.jsonl": ["id", "input", "expected_behavior"],
    "stale_info_regression.jsonl": ["id", "input", "expected_doc_id"],
}


def validate() -> list[str]:
    errors = []
    for filename, required in REQUIRED_FIELDS.items():
        path = DATASET_DIR / filename
        if not path.exists():
            errors.append(f"MISSING dataset file: {filename}")
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                errors.append(f"{filename}:{lineno} invalid JSON: {e}")
                continue
            for field in required:
                if field not in obj:
                    errors.append(f"{filename}:{lineno} (id={obj.get('id', '?')}) missing required field '{field}'")
    return errors


if __name__ == "__main__":
    errs = validate()
    if errs:
        print(f"FAILED: {len(errs)} dataset validation error(s)")
        for e in errs:
            print(f"  - {e}")
        sys.exit(1)
    print("All evaluation datasets valid.")
