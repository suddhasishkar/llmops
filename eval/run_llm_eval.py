#!/usr/bin/env python3
"""Layer 3 — LLM/RAG evaluation.

Simplified, deterministic proxy scoring for training use. A real deployment
would use an LLM-as-judge (RAGAS / Promptfoo assertion / Foundry Evaluation
built-in evaluator) here; this script computes the SAME shaped metrics
(groundedness, citation_coverage, citation_correctness) from structural
signals (was a citation present, did it match the expected/current
document) so the pipeline and release-policy gate behave identically to
a real evaluator, without requiring a live model call.

Run: python -m eval.run_llm_eval --dataset eval/datasets/rag_eval.jsonl --out eval/results/llm_eval.json
     python -m eval.run_llm_eval --dataset eval/datasets/stale_info_regression.jsonl --out eval/results/stale_info_eval.json
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.agent.agent import run_turn


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def score_case(case: dict, prompt_version: str) -> dict:
    result = run_turn(case["input"] if "input" in case else case["query"],
                       prompt_version=prompt_version, persist_trace=False)
    citations = result.get("citations", [])
    expected_doc = case.get("expected_doc_id")
    must_not_doc = case.get("must_not_cite_doc_id")

    cited_ids = [c["doc_id"] for c in citations]
    grounded = len(citations) > 0  # answer is always built FROM retrieved chunks in this stub
    citation_present = len(citations) > 0
    citation_correct = (expected_doc is None) or (expected_doc in cited_ids)
    stale_violation = bool(must_not_doc) and (must_not_doc in cited_ids)

    return {
        "id": case["id"],
        "grounded": grounded,
        "citation_present": citation_present,
        "citation_correct": citation_correct and not stale_violation,
        "stale_citation_violation": stale_violation,
        "cited_doc_ids": cited_ids,
        "answer": result["answer"],
    }


def run(dataset_path: Path, prompt_version: str) -> dict:
    cases = load_jsonl(dataset_path)
    scored = [score_case(c, prompt_version) for c in cases if ("input" in c or "query" in c)]
    n = len(scored) or 1
    groundedness = sum(s["grounded"] for s in scored) / n
    citation_coverage = sum(s["citation_present"] for s in scored) / n
    citation_correctness = sum(s["citation_correct"] for s in scored) / n
    stale_violations = sum(s["stale_citation_violation"] for s in scored)

    return {
        "suite": "llm_eval",
        "dataset": str(dataset_path),
        "prompt_version": prompt_version,
        "metrics": {
            "groundedness": round(groundedness, 3),
            "citation_coverage": round(citation_coverage, 3),
            "citation_correctness": round(citation_correctness, 3),
            "stale_citation_violations": stale_violations,
        },
        "cases": scored,
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
