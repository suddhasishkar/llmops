#!/usr/bin/env python3
"""LLM-as-judge evaluation -- the real model-graded evaluator
`eval/run_llm_eval.py`'s own docstring names as the thing a real
deployment would use instead of structural proxy scoring. See
docs/adr/0004-llmops-agentops-rigor.md.

This makes a SEPARATE real model call per case (through the same
LiteLLM gateway every agent role uses -- app/agent/azure_openai_client.py)
after the real agent turn, asking the model to grade its own answer
against the retrieved context on a 1-5 rubric for groundedness and
helpfulness. This is genuinely model-graded, not a structural proxy --
and that comes with genuinely model-graded weaknesses: judge scores can
drift between runs, be gamed by verbose-but-empty answers, or disagree
with a human reviewer. That's exactly why this suite is INFORMATIONAL
in this pass, not wired into release-policy.yaml as a blocking gate --
see the ADR's Decision section for why: a judge score needs real
calibration runs against known-good/known-bad cases before an
organization should trust it to block a release, and this repo doesn't
have that calibration history yet. `eval/post_pr_summary.py` surfaces
these scores on every PR so a human sees the trend immediately, which is
the right amount of rigor for a first pass -- promote to a blocking gate
once you've watched it agree with human judgment across enough real
runs to trust the threshold you'd set.

Run: python -m eval.run_llm_judge_eval --dataset eval/datasets/rag_eval.jsonl --out eval/results/llm_judge_eval.json
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.agent.agent import run_turn
from app.agent.azure_openai_client import get_client, get_deployment_name

JUDGE_SYSTEM_PROMPT = """You are a strict evaluator grading a telecom support agent's answer.
Score two dimensions, each 1-5 (5 is best):
- groundedness: is every claim in the answer actually supported by the provided retrieved context? An answer that states anything not present in the context scores 1-2, regardless of how plausible it sounds.
- helpfulness: does the answer actually address the customer's question, clearly and completely, given what the context allows?

Respond with ONLY a JSON object: {"groundedness": <1-5 int>, "helpfulness": <1-5 int>, "rationale": "<one sentence>"}
No other text."""


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def judge_case(case: dict, prompt_version: str) -> dict:
    user_message = case["input"] if "input" in case else case["query"]
    result = run_turn(user_message, prompt_version=prompt_version, persist_trace=False)
    context_summary = "; ".join(f"[{c['doc_id']}]" for c in result.get("citations", [])) or "(no citations retrieved)"

    judge_user_prompt = (
        f"Customer question: {user_message}\n\n"
        f"Retrieved context (by doc_id, full text not shown here -- judge whether the answer's claims are "
        f"consistent with having been drawn from real policy documents, not fabricated): {context_summary}\n\n"
        f"Agent's answer: {result['answer']}"
    )

    client = get_client()
    try:
        response = client.chat.completions.create(
            model=get_deployment_name(),
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": judge_user_prompt},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(response.choices[0].message.content)
        groundedness = int(parsed.get("groundedness", 0))
        helpfulness = int(parsed.get("helpfulness", 0))
        rationale = str(parsed.get("rationale", ""))
        judge_error = None
    except Exception as e:
        # A judge-call failure (bad JSON, gateway error, etc.) must not
        # crash the whole eval run -- it's recorded as a failed judgment
        # for this one case, not silently dropped and not fatal.
        groundedness = 0
        helpfulness = 0
        rationale = ""
        judge_error = str(e)

    return {
        "id": case["id"],
        "answer": result["answer"],
        "judge_groundedness": groundedness,
        "judge_helpfulness": helpfulness,
        "judge_rationale": rationale,
        "judge_error": judge_error,
    }


def run(dataset_path: Path, prompt_version: str) -> dict:
    cases = load_jsonl(dataset_path)
    scored = [judge_case(c, prompt_version) for c in cases if ("input" in c or "query" in c)]
    valid = [s for s in scored if s["judge_error"] is None]
    n = len(valid) or 1

    # Normalized to 0-1 (raw score / 5) to sit alongside this repo's
    # other 0-1 metrics (groundedness, citation_coverage) even though
    # the judge itself reasons on a 1-5 scale -- see release-policy.yaml
    # if/when this is promoted to a blocking threshold.
    avg_judge_groundedness = round(sum(s["judge_groundedness"] for s in valid) / n / 5, 3)
    avg_judge_helpfulness = round(sum(s["judge_helpfulness"] for s in valid) / n / 5, 3)
    judge_call_failures = len(scored) - len(valid)

    return {
        "suite": "llm_judge_eval",
        "dataset": str(dataset_path),
        "prompt_version": prompt_version,
        "informational_only": True,  # see module docstring -- not a release-policy.yaml gate yet
        "metrics": {
            "llm_judge_groundedness": avg_judge_groundedness,
            "llm_judge_helpfulness": avg_judge_helpfulness,
            "llm_judge_call_failures": judge_call_failures,
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
