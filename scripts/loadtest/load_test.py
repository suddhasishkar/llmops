#!/usr/bin/env python3
"""
Minimal load test for a deployed Nimbus Support Copilot Container App.

Why this exists: 03_Day2_Governance_Monitoring_LLMOps_AgentOps.md talks
about p95 latency, and infra/main.bicep's `latencyAlert` pages someone
when live p95 crosses the same 3500ms threshold release-policy.yaml
gates a release on -- but until this script existed, nobody in this
program had ever actually generated a real number to check against that
threshold. See planning/Interview_Readiness_Enrichment_Plan.md item A5.

WHAT THIS SCRIPT DELIBERATELY DOES NOT DO: report a canned "expected"
result. Run it yourself against your own deployment (see README.md "Real
Azure deployment" for how to stand one up) and the number it prints is a
REAL number you produced, that you can defend in an interview -- "here's
what happened when I put N concurrent users against it" is a much
stronger answer than reciting a number you read somewhere. Everyone's
Container App scale settings, region, and Azure OpenAI capacity are
different; there is no single correct number to cite.

Uses only the Python standard library (threading + urllib) on purpose --
no new dependency for something this simple, and it keeps this runnable
in the exact same environment as everything else in this repo.

Usage:
    python3 scripts/loadtest/load_test.py --url https://<your-app>.azurecontainerapps.io \
        --concurrency 10 --requests 50
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

SAMPLE_MESSAGES = [
    "What is the cancellation window?",
    "My bill seems wrong, can I get some money back?",
    "Is there an outage in my area?",
    "What's my current plan?",
    "Please compensate me for the outage last week.",
]


def _one_request(url: str, message: str) -> dict:
    payload = json.dumps({"message": message, "customer_id": "CUST-1002"}).encode("utf-8")
    req = urllib.request.Request(
        url.rstrip("/") + "/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
            status = resp.status
    except urllib.error.HTTPError as e:
        status = e.code
    except Exception:
        status = 0
    elapsed_ms = (time.time() - t0) * 1000
    return {"status": status, "elapsed_ms": elapsed_ms}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Base URL of the deployed Container App (containerAppFqdn output, with https://)")
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--requests", type=int, default=50)
    args = parser.parse_args()

    results = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(_one_request, args.url, SAMPLE_MESSAGES[i % len(SAMPLE_MESSAGES)])
            for i in range(args.requests)
        ]
        for f in as_completed(futures):
            results.append(f.result())

    latencies = sorted(r["elapsed_ms"] for r in results)
    error_count = sum(1 for r in results if r["status"] not in (200,))

    def pctl(p):
        if not latencies:
            return 0.0
        idx = min(len(latencies) - 1, int(len(latencies) * p / 100))
        return latencies[idx]

    print(json.dumps({
        "url": args.url,
        "total_requests": len(results),
        "concurrency": args.concurrency,
        "error_count": error_count,
        "error_rate": round(error_count / len(results), 4) if results else 0.0,
        "p50_ms": round(pctl(50), 1),
        "p95_ms": round(pctl(95), 1),
        "p99_ms": round(pctl(99), 1),
        "mean_ms": round(statistics.mean(latencies), 1) if latencies else 0.0,
        "against_release_policy_p95_threshold_ms": 3500,
        "note": "This is a real measurement from THIS run against THIS deployment -- "
                "re-run it yourself rather than quoting this output verbatim.",
    }, indent=2))


if __name__ == "__main__":
    main()
