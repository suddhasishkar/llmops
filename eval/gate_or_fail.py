#!/usr/bin/env python3
"""Fails the CI job (non-zero exit) unless the decision is 'promote'.
Kept separate from apply_release_policy.py so the PR-comment step can run
(and be visible) even when the pipeline is about to fail the job.
Run: python -m eval.gate_or_fail --decision eval/results/decision.json
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision", type=Path, required=True)
    args = parser.parse_args()
    decision = json.loads(args.decision.read_text())
    if decision["decision"] != "promote":
        print(f"Release gate FAILED: decision = {decision['decision'].upper()}")
        sys.exit(1)
    print("Release gate PASSED: decision = PROMOTE")
