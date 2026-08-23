#!/usr/bin/env python3
"""Formats eval/results/decision.json as a Markdown PR comment.
In real CI, pipe this to `gh pr comment` or the GitHub Actions PR-comment
action. Kept as a standalone formatter here so it's testable without a
live GitHub context.
Run: python -m eval.post_pr_summary --decision eval/results/decision.json
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

ICONS = {"promote": "✅", "hold": "\U0001F7E0", "reject": "\U0001F534"}


def format_summary(decision: dict, judge_report: dict | None = None) -> str:
    icon = ICONS.get(decision["decision"], "❓")
    lines = [f"## {icon} AI Release Decision: **{decision['decision'].upper()}**", ""]
    if decision["breaches"]:
        lines.append("| Metric | Value | Bound | Action |")
        lines.append("|---|---|---|---|")
        for b in decision["breaches"]:
            lines.append(f"| {b['metric']} | {b['value']} | {b['bound_type']} {b['bound']} | {b['on_breach']} |")
    else:
        lines.append("All release-policy thresholds passed.")
    lines.append("")
    lines.append("_Thresholds in `release-policy.yaml` are training illustrations, not universal production standards._")

    if judge_report is not None:
        # LLM-as-judge scores -- informational only, never gates the
        # decision above. See eval/run_llm_judge_eval.py's docstring and
        # docs/adr/0004-llmops-agentops-rigor.md for why.
        m = judge_report.get("metrics", {})
        lines.append("")
        lines.append("### \U0001F9D1‍⚖️ LLM-as-judge scores (informational only -- not a release gate)")
        lines.append("| Metric | Score |")
        lines.append("|---|---|")
        lines.append(f"| Groundedness (judge) | {m.get('llm_judge_groundedness', 'n/a')} |")
        lines.append(f"| Helpfulness (judge) | {m.get('llm_judge_helpfulness', 'n/a')} |")
        lines.append(f"| Judge call failures | {m.get('llm_judge_call_failures', 'n/a')} |")

    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision", type=Path, required=True)
    parser.add_argument("--judge-report", type=Path, default=None, help="Optional eval/results/llm_judge_eval.json -- informational section only")
    args = parser.parse_args()
    decision = json.loads(args.decision.read_text())
    judge_report = json.loads(args.judge_report.read_text()) if args.judge_report and args.judge_report.exists() else None
    print(format_summary(decision, judge_report))
