#!/usr/bin/env python3
"""Layer 4 — agent trajectory / tool-selection evaluation.

Compares the agent's actual tool call against the dataset's expected
trajectory. This is the check that catches the Day 1 lab's injected
fault: run with --prompt-version candidate_broken to reproduce the HOLD,
and --prompt-version candidate_fixed (or baseline) to reproduce PROMOTE.

Run: python -m eval.run_agent_trajectory_eval --dataset eval/datasets/tool_trajectory_eval.jsonl --prompt-version candidate_broken --out eval/results/agent_eval.json
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.agent.agent import run_turn


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run(dataset_path: Path, prompt_version: str) -> dict:
    cases = load_jsonl(dataset_path)
    results = []
    unauthorized_actions = 0

    for case in cases:
        t0 = time.time()
        result = run_turn(case["input"], prompt_version=prompt_version, persist_trace=False)
        latency_ms = round((time.time() - t0) * 1000, 1)
        actual_tool = result["tool_call"]["name"] if result["tool_call"] else None
        expected_first_tool = case["expected_trajectory"][0] if case.get("expected_trajectory") else None
        tool_selection_correct = actual_tool == expected_first_tool

        # A tool call that raised a ToolAuthorizationError (blocked) never
        # counts as an unauthorized action -- it's the control working.
        executed_without_block = result["tool_call"] is not None and result["tool_error"] is None
        is_credit_call = actual_tool == "request_customer_credit"
        credit_was_expected = expected_first_tool == "request_customer_credit"
        unauthorized = is_credit_call and executed_without_block and not credit_was_expected
        if unauthorized:
            unauthorized_actions += 1

        results.append({
            "id": case["id"], "input": case["input"],
            "expected_first_tool": expected_first_tool, "actual_tool": actual_tool,
            "tool_selection_correct": tool_selection_correct,
            "step_count": result["step_count"], "unauthorized_action": unauthorized,
            "latency_ms": latency_ms, "estimated_cost_usd": result["estimated_cost_usd"],
        })

    n = len(results) or 1
    tool_selection_accuracy = sum(r["tool_selection_correct"] for r in results) / n
    unauthorized_action_rate = unauthorized_actions / n
    max_steps = max((r["step_count"] for r in results), default=0)

    # Cost/latency gating -- see docs/adr/0004-llmops-agentops-rigor.md and
    # release-policy.yaml. avg_cost_per_turn_usd is a per-turn ESTIMATE
    # (see app/agent/cost_tracking.py's own honesty note, not billed
    # usage); p95_latency_ms is real wall-clock time around each real
    # run_turn() call in THIS process (includes real Foundry/Search/
    # Content Safety network round-trips), computed with the
    # nearest-rank method over this dataset's sample size -- accurate
    # enough to catch a regression trend on a ~26-30 case dataset, not a
    # statistically rigorous percentile estimate (would need a much
    # larger sample for that).
    latencies = sorted(r["latency_ms"] for r in results)
    p95_index = max(0, min(len(latencies) - 1, round(0.95 * (len(latencies) - 1))))
    p95_latency_ms = latencies[p95_index] if latencies else 0
    avg_cost_per_turn_usd = round(sum(r["estimated_cost_usd"] for r in results) / n, 8)

    return {
        "suite": "agent_trajectory_eval",
        "dataset": str(dataset_path),
        "prompt_version": prompt_version,
        "metrics": {
            "tool_selection_accuracy": round(tool_selection_accuracy, 3),
            "unauthorized_action_rate": round(unauthorized_action_rate, 3),
            "max_agent_steps_observed": max_steps,
            "p95_latency_ms": p95_latency_ms,
            "avg_cost_per_turn_usd": avg_cost_per_turn_usd,
        },
        "cases": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--prompt-version", default="baseline")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    report = run(args.dataset, args.prompt_version)
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    print(text)
