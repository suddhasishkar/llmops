#!/usr/bin/env python3
"""
PLACEHOLDER / MOCK security-scanning stage.

This is NOT a real SAST, secrets, dependency-CVE, or container-CVE
scanner. It is a deliberately clearly-labeled stand-in that occupies the
exact position a real scanning tool would occupy in the pipeline
(ai-release.yml's `code-scanning` job), so this repository demonstrates
the SHAPE of a moderately mature code-scanning gate -- categories,
pass/fail semantics, an evidence artifact, a PR-postable summary --
without claiming security coverage this training/reference repo has not
actually earned.

Real swap-in points, unchanged in position and gate semantics:
  - SAST              -> Semgrep (`p/python` + `p/owasp-top-ten`), or
                          SonarQube's own SAST engine via sonar-scanner
  - Secrets scanning   -> Gitleaks, or SonarQube Enterprise secrets
                          detection / GitGuardian
  - Dependency CVEs    -> pip-audit (already present elsewhere in this
                          pipeline, non-blocking today) + Trivy filesystem
                          scan, or Snyk Open Source
  - Container CVEs     -> Trivy image scan against the built ACR image,
                          or Snyk Container / Prisma Cloud
  - SBOM               -> Syft (SPDX/CycloneDX)
See docs/adr/0001-foundry-and-multiagent.md and the platform maturity
roadmap's Section 4.1 for the full mapping.

Run: python -m scripts.mock_security_scan --out eval/results/scan_report.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CATEGORIES = [
    {
        "category": "sast",
        "real_tool_swap_in": "Semgrep (p/python + p/owasp-top-ten) or SonarQube sonar-scanner",
        "findings": [],
    },
    {
        "category": "secrets",
        "real_tool_swap_in": "Gitleaks or SonarQube Enterprise secrets detection",
        "findings": [],
    },
    {
        "category": "dependency_cve",
        "real_tool_swap_in": "Trivy filesystem scan or Snyk Open Source",
        "findings": [],
    },
    {
        "category": "container_cve",
        "real_tool_swap_in": "Trivy image scan or Snyk Container",
        "findings": [],
    },
    {
        "category": "iac",
        "real_tool_swap_in": "Checkov against infra/*.bicep",
        "findings": [],
    },
]


def run() -> dict:
    return {
        "stage": "code-scanning",
        "mode": "PLACEHOLDER_MOCK",
        "warning": (
            "This report is fabricated by scripts/mock_security_scan.py, a "
            "placeholder for presentation purposes. It performed NO real "
            "static analysis, secrets detection, or CVE scanning. Do not "
            "treat a PASS here as a real security signal -- see the "
            "'real_tool_swap_in' field on each category for what would "
            "actually run in a production build of this pipeline."
        ),
        "categories": CATEGORIES,
        "decision": "PASS",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    report = run()
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)

    print("=" * 72)
    print("  PLACEHOLDER SECURITY SCAN -- NOT A REAL SCANNER (see --out report)")
    print("=" * 72)
    for c in report["categories"]:
        print(f"  [MOCK PASS] {c['category']:<16} swap-in: {c['real_tool_swap_in']}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
