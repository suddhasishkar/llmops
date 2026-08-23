#!/usr/bin/env python3
"""TRAINER USE ONLY -- stages the Day 2 lab's real stale-index incident
against the LIVE Azure AI Search index: for every document that has been
superseded, this deletes the CURRENT (correct) version from the index
and re-uploads the OLD (superseded) version in its place -- simulating
"a reindex job pushed the wrong content and nobody caught it." This is a
real mutation of real cloud state; there is no local simulation involved
in this rebuild.

Never run this against anything other than a disposable lab
environment; it deliberately leaves the retrieval index wrong.

Usage: python -m scripts.inject_stale_doc
Verify with: python -m eval.check_index_freshness   (expect: healthy=false)
Fix with:    python -m scripts.build_search_index    (expect: healthy=true after)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.retrieval import retrieval

if __name__ == "__main__":
    docs = retrieval.load_all_documents()
    superseded_ids = {d.supersedes for d in docs if d.supersedes}
    stale_docs = [d for d in docs if d.doc_id in superseded_ids]
    current_docs = [d for d in docs if d.supersedes in superseded_ids]  # the doc(s) that superseded them
    if not stale_docs:
        print("No superseded document found in knowledge_docs/ -- nothing to inject.")
        sys.exit(1)

    client = retrieval._get_search_client()

    # 1. Delete the CURRENT, correct version(s) from the index.
    client.delete_documents(documents=[{"doc_id": d.doc_id} for d in current_docs])

    # 2. Re-upload the OLD, superseded version(s) in their place.
    payload = [
        {
            "doc_id": d.doc_id,
            "path": d.path,
            "effective_date": d.effective_date,
            "policy_owner": d.policy_owner,
            "chunk": d.text,
        }
        for d in stale_docs
    ]
    client.merge_or_upload_documents(documents=payload)

    print(f"Injected fault: deleted current doc_id(s) {[d.doc_id for d in current_docs]} from the LIVE "
          f"search index and replaced them with superseded doc_id(s) {[d.doc_id for d in stale_docs]}. "
          f"Verify with: python -m eval.check_index_freshness (expect healthy=false)")
