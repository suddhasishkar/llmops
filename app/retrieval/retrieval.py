"""
Retrieval layer for the Nimbus Support Agent — always a real Azure AI
Search index. No local TF-IDF fallback, no local index-state JSON file.

Earlier versions of this repo kept a free local TF-IDF retriever as the
default and treated Azure AI Search as an opt-in "enrichment" backend,
specifically so a local JSON file could simulate a stale-index incident
without any cloud dependency. That trade-off is gone in this rebuild:
`retrieve()` always calls the real index, and the stale-index lab (Day
2) now injects and detects a real fault against real cloud state — see
`check_freshness()`/`list_indexed_doc_ids()` below and
`scripts/inject_stale_doc.py`. If you're looking for why this file used
to have an `if backend == "azure_search"` branch, it doesn't anymore:
there is one implementation, because there is one thing this program now
means by "retrieval."

Index schema (created by `scripts/build_search_index.py`, the same
script that keeps it correct): `doc_id` (key), `path`, `effective_date`,
`policy_owner` — all filterable simple fields — and `chunk`, a
full-text-searchable field holding the document body. Plain keyword
(BM25) search via `search_text`, not vector/hybrid — a deliberate,
named scoping decision (see that script's docstring), not an oversight.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_DIR = REPO_ROOT / "knowledge_docs"
SEARCH_INDEX_NAME = os.getenv("AZURE_SEARCH_INDEX", "nimbus-knowledge-docs")

FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


@dataclass
class Document:
    doc_id: str
    path: str
    effective_date: str
    policy_owner: str
    supersedes: Optional[str]
    text: str


def _parse_front_matter(raw: str, path: Path) -> Document:
    m = FRONT_MATTER_RE.match(raw)
    if not m:
        raise ValueError(f"{path} is missing required YAML-style front matter")
    fm_block, body = m.group(1), m.group(2)
    fields = {}
    for line in fm_block.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip('"')
    for required in ("doc_id", "effective_date", "policy_owner"):
        if required not in fields:
            raise ValueError(f"{path} front matter missing required field: {required}")
    return Document(
        doc_id=fields["doc_id"],
        path=str(path.relative_to(REPO_ROOT)),
        effective_date=fields["effective_date"],
        policy_owner=fields["policy_owner"],
        supersedes=fields.get("supersedes") or None,
        text=body.strip(),
    )


def load_all_documents() -> list[Document]:
    """Every document ON DISK — the source of truth `check_freshness()`
    compares the live index against. This is NOT the same as what's
    currently indexed; see `list_indexed_doc_ids()`.
    """
    docs = []
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        docs.append(_parse_front_matter(path.read_text(), path))
    return docs


def expected_live_doc_ids() -> list[str]:
    """Every document ON DISK that nothing else supersedes — the correct,
    current set the index should contain right now.
    """
    docs = load_all_documents()
    superseded_ids = {d.supersedes for d in docs if d.supersedes}
    return sorted(d.doc_id for d in docs if d.doc_id not in superseded_ids)


def _get_search_client():
    try:
        from azure.search.documents import SearchClient
    except ImportError as e:
        raise ImportError(
            "The 'azure-search-documents' package is required. Install it with: "
            "pip install azure-search-documents azure-identity"
        ) from e

    endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
    if not endpoint:
        raise RuntimeError(
            "AZURE_SEARCH_ENDPOINT is not set. `azd up` writes this "
            "automatically -- see Day1_Lab_Guide.md Part 0."
        )

    api_key = os.getenv("AZURE_SEARCH_API_KEY")
    if api_key:
        from azure.core.credentials import AzureKeyCredential
        credential = AzureKeyCredential(api_key)
    else:
        from azure.identity import DefaultAzureCredential
        credential = DefaultAzureCredential(managed_identity_client_id=os.getenv("AZURE_CLIENT_ID"))

    return SearchClient(endpoint=endpoint, index_name=SEARCH_INDEX_NAME, credential=credential)


def retrieve(query: str, k: int = 3) -> list[dict]:
    """Real Azure AI Search keyword (BM25) query. Returns a list of dicts
    with doc_id/path/effective_date/policy_owner/score/chunk.
    """
    client = _get_search_client()
    results = client.search(search_text=query, top=k, query_type="simple")
    out = []
    for r in results:
        out.append({
            "doc_id": r.get("doc_id"),
            "path": r.get("path"),
            "effective_date": r.get("effective_date"),
            "policy_owner": r.get("policy_owner"),
            "score": float(r.get("@search.score", 0.0)),
            "chunk": (r.get("chunk") or "")[:400],
        })
    return out


def list_indexed_doc_ids() -> list[str]:
    """What the live index actually contains RIGHT NOW — may be stale.
    Uses a match-all query with a small `select` — the standard approach
    for a corpus this size (a handful of documents); a production-scale
    index would page this or query the indexer's own run-history API
    instead of listing every document.
    """
    client = _get_search_client()
    results = client.search(search_text="*", select=["doc_id"], top=1000)
    return sorted({r["doc_id"] for r in results if r.get("doc_id")})


def check_freshness() -> dict:
    """Compares the live index's actual contents against the source of
    truth on disk — directly, not by trusting a job's reported status.
    This is the fixed version of the Day 2 incident's alerting rule: the
    original, buggy version only checked "did a reindex job run
    successfully," which stays green even when the job forgot to evict a
    superseded document. Checking drift directly, against real indexed
    content, is what actually catches it.
    """
    expected = expected_live_doc_ids()
    indexed = list_indexed_doc_ids()
    drift_ids = sorted(set(expected) ^ set(indexed))
    return {
        "healthy": len(drift_ids) == 0,
        "drift_count": len(drift_ids),
        "drift_doc_ids": drift_ids,
        "expected_live_doc_ids": expected,
        "currently_indexed_doc_ids": indexed,
    }
