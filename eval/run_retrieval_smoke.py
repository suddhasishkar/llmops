#!/usr/bin/env python3
"""Layer 2 — retrieval smoke test. Known query -> expected document.
Run: python -m eval.run_retrieval_smoke [--query "..."] [--dataset PATH]
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.retrieval import retrieval

DEFAULT_DATASET = Path(__file__).parent / "datasets" / "rag_eval.jsonl"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run(dataset_path: Path) -> dict:
    cases = [c for c in load_jsonl(dataset_path) if c.get("expected_doc_id")]
    results = []
    for case in cases:
        hits = retrieval.retrieve(case["query"], k=3)
        top_doc_id = hits[0]["doc_id"] if hits else None
        passed = top_doc_id == case["expected_doc_id"]
        results.append({"id": case["id"], "query": case["query"], "expected": case["expected_doc_id"],
                         "actual_top_doc": top_doc_id, "passed": passed})
    pass_rate = sum(r["passed"] for r in results) / len(results) if results else 0.0
    return {"suite": "retrieval_smoke", "pass_rate": pass_rate, "cases": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--query", default=None, help="Ad-hoc single query instead of the dataset")
    args = parser.parse_args()

    if args.query:
        print(json.dumps(retrieval.retrieve(args.query, k=3), indent=2))
    else:
        report = run(args.dataset)
        print(json.dumps(report, indent=2))
        sys.exit(0 if report["pass_rate"] == 1.0 else 1)
