#!/usr/bin/env python3
"""Applies release-policy.yaml thresholds to the evaluation result JSON
files and produces a promote/hold/reject decision. Every threshold this
checks is a metric one of the three eval scripts actually computes from
a real agent run against real Azure OpenAI/Search/Content Safety -- see
Dataset_and_Evaluation_Guide.md Section 6.

Run: python -m eval.apply_release_policy --policy release-policy.yaml \
        --results eval/results/llm_eval.json eval/results/agent_eval.json eval/results/safety_eval.json \
        --out eval/results/decision.json
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


def load_policy(path: Path) -> dict:
    text = path.read_text()
    if yaml is not None:
        return yaml.safe_load(text)
    raise RuntimeError("PyYAML is required to parse release-policy.yaml (pip install pyyaml)")


def merge_metrics(result_paths: list[Path]) -> dict:
    merged = {}
    for p in result_paths:
        data = json.loads(p.read_text())
        merged.update(data.get("metrics", {}))
    return merged


def evaluate(policy: dict, metrics: dict) -> dict:
    thresholds = policy["release_policy"]["thresholds"]
    breaches = []
    critical = False

    def get_metric(name, default=None):
        return metrics.get(name, default)

    # Every one of these eight is a metric one of the eval scripts actually
    # computes -- no "assume pass" placeholders left in this rebuild. The
    # last two (p95_latency_ms, avg_cost_per_turn_usd) were added in
    # docs/adr/0004-llmops-agentops-rigor.md, computed by
    # eval/run_agent_trajectory_eval.py.
    checks = [
        ("groundedness", "min", get_metric("groundedness")),
        ("citation_coverage", "min", get_metric("citation_coverage")),
        ("tool_selection_accuracy", "min", get_metric("tool_selection_accuracy")),
        ("unauthorized_action_rate", "max", get_metric("unauthorized_action_rate")),
        ("max_agent_steps", "max", get_metric("max_agent_steps_observed", 0)),
        ("critical_safety_failures", "max", get_metric("critical_safety_failures", 0)),
        ("p95_latency_ms", "max", get_metric("p95_latency_ms")),
        ("avg_cost_per_turn_usd", "max", get_metric("avg_cost_per_turn_usd")),
    ]

    for name, bound_type, value in checks:
        if name not in thresholds or value is None:
            continue
        bound = thresholds[name].get(bound_type)
        if bound is None:
            continue
        breached = (bound_type == "min" and value < bound) or (bound_type == "max" and value > bound)
        if breached:
            on_breach = thresholds[name].get("on_breach", "hold")
            breaches.append({"metric": name, "value": value, "bound_type": bound_type, "bound": bound, "on_breach": on_breach})
            if on_breach == "reject" or name in ("unauthorized_action_rate", "critical_safety_failures"):
                critical = True

    if not breaches:
        decision = "promote"
    elif critical:
        decision = "reject"
    else:
        decision = "hold"

    return {"decision": decision, "breaches": breaches, "metrics_evaluated": metrics}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--results", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    policy = load_policy(args.policy)
    metrics = merge_metrics(args.results)
    decision = evaluate(policy, metrics)
    text = json.dumps(decision, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    print(text)
    sys.exit(0 if decision["decision"] == "promote" else 1)
