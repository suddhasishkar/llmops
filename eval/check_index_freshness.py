#!/usr/bin/env python3
"""CLI wrapper around app.retrieval.retrieval.check_freshness -- queries
the REAL Azure AI Search index and compares it against knowledge_docs/
on disk. Requires AZURE_SEARCH_ENDPOINT (set automatically by `azd up`).
Run: python -m eval.check_index_freshness
"""
from __future__ import annotations
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.retrieval import retrieval

if __name__ == "__main__":
    report = retrieval.check_freshness()
    print(json.dumps(report, indent=2))
    sys.exit(0 if report["healthy"] else 1)
