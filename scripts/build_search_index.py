#!/usr/bin/env python3
"""Creates (or updates) the real Azure AI Search index and makes its
contents match knowledge_docs/ on disk EXACTLY -- uploading every live
document AND deleting any indexed document that no longer belongs
(anything not in `retrieval.expected_live_doc_ids()`).

This is the one script that can always make the index correct. Run it:
  - once, right after `azd up` (the azd postprovision hook calls this
    automatically -- see azure.yaml)
  - again, any time, to FIX a drifted index -- including after
    scripts/inject_stale_doc.py has deliberately broken it for the Day 2
    lab

Requires: AZURE_SEARCH_ENDPOINT (set automatically by `azd up`), and
either AZURE_SEARCH_API_KEY or `az login`'d / managed-identity
credentials with Search Service Contributor + Search Index Data
Contributor on the target service.

Run: python -m scripts.build_search_index
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.retrieval import retrieval


def _get_credential():
    api_key = os.getenv("AZURE_SEARCH_API_KEY")
    if api_key:
        from azure.core.credentials import AzureKeyCredential
        return AzureKeyCredential(api_key)
    from azure.identity import DefaultAzureCredential
    return DefaultAzureCredential(managed_identity_client_id=os.getenv("AZURE_CLIENT_ID"))


def ensure_index_exists() -> None:
    from azure.search.documents.indexes import SearchIndexClient
    from azure.search.documents.indexes.models import SearchIndex, SimpleField, SearchableField, SearchFieldDataType

    endpoint = os.environ["AZURE_SEARCH_ENDPOINT"]
    index_client = SearchIndexClient(endpoint=endpoint, credential=_get_credential())
    fields = [
        SimpleField(name="doc_id", type=SearchFieldDataType.String, key=True, filterable=True),
        SimpleField(name="path", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="effective_date", type=SearchFieldDataType.String, filterable=True, sortable=True),
        SimpleField(name="policy_owner", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="chunk", type=SearchFieldDataType.String),
    ]
    index_client.create_or_update_index(SearchIndex(name=retrieval.SEARCH_INDEX_NAME, fields=fields))


def sync_index() -> dict:
    """Uploads every current, non-superseded document, and deletes any
    indexed document that shouldn't be there. Returns a small report.
    """
    docs = retrieval.load_all_documents()
    live_ids = set(retrieval.expected_live_doc_ids())
    live_docs = [d for d in docs if d.doc_id in live_ids]

    client = retrieval._get_search_client()

    upload_payload = [
        {
            "doc_id": d.doc_id,
            "path": d.path,
            "effective_date": d.effective_date,
            "policy_owner": d.policy_owner,
            "chunk": d.text,
        }
        for d in live_docs
    ]
    client.merge_or_upload_documents(documents=upload_payload)

    currently_indexed = set(retrieval.list_indexed_doc_ids())
    stale_ids = currently_indexed - live_ids
    if stale_ids:
        client.delete_documents(documents=[{"doc_id": i} for i in stale_ids])

    return {
        "uploaded_doc_ids": sorted(d.doc_id for d in live_docs),
        "evicted_doc_ids": sorted(stale_ids),
    }


if __name__ == "__main__":
    import json
    ensure_index_exists()
    report = sync_index()
    print(json.dumps(report, indent=2))
    freshness = retrieval.check_freshness()
    print(json.dumps(freshness, indent=2))
    sys.exit(0 if freshness["healthy"] else 1)
