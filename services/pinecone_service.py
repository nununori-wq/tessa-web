"""
Pinecone integration for TESSA's knowledge / RAG system.

Responsible for:
  - lazily initializing the Pinecone client
  - creating the TESSA knowledge index only if it doesn't already exist
  - ingesting approved IRD Grenada knowledge (FAQs, rulebook, procedures, tax info)
  - searching that knowledge at query time and returning it as LLM-ready context

All Pinecone calls are wrapped so a missing key, a down Pinecone service, or a
bad response degrades gracefully to "no knowledge found" rather than crashing
the /chat endpoint.
"""

import os
import logging
from typing import Dict, List, Optional

from pinecone import Pinecone

logger = logging.getLogger("chatbot")

INDEX_NAME = "tessa-knowledge"
NAMESPACE = "rules"
CLOUD = "aws"
REGION = "us-east-1"
EMBED_MODEL = "llama-text-embed-v2"

_pc: Optional[Pinecone] = None
_index = None


class PineconeConfigError(Exception):
    """Raised when Pinecone can't be configured (e.g. missing API key)."""


def _get_client() -> Pinecone:
    """Create (once) and return the Pinecone client. Never logs the key itself."""
    global _pc
    if _pc is not None:
        return _pc

    api_key = os.getenv("PINECONE_API_KEY")
    if not api_key:
        raise PineconeConfigError(
            "PINECONE_API_KEY is not set. Add it to your .env file."
        )

    try:
        _pc = Pinecone(api_key=api_key)
    except Exception as exc:
        logger.error("Failed to initialize Pinecone client: %s", exc)
        raise PineconeConfigError("Could not initialize the Pinecone client.") from exc

    return _pc


def _existing_index_names(pc: Pinecone) -> List[str]:
    """Handle both list-of-objects and .names()-style responses across SDK versions."""
    try:
        listing = pc.list_indexes()
        if hasattr(listing, "names"):
            return list(listing.names())
        return [idx["name"] if isinstance(idx, dict) else idx.name for idx in listing]
    except Exception as exc:
        logger.error("Failed to list Pinecone indexes: %s", exc)
        return []


def init_index():
    """
    Ensure the TESSA knowledge index exists, creating it only if it's missing.
    Safe to call on every FastAPI startup — connects instead of recreating
    when the index is already there.
    """
    global _index

    try:
        pc = _get_client()
    except PineconeConfigError as exc:
        logger.warning("Pinecone not configured — knowledge retrieval disabled: %s", exc)
        _index = None
        return None

    existing = _existing_index_names(pc)

    if INDEX_NAME not in existing:
        try:
            logger.info("Pinecone index '%s' not found — creating it.", INDEX_NAME)
            pc.create_index_for_model(
                name=INDEX_NAME,
                cloud=CLOUD,
                region=REGION,
                embed={
                    "model": EMBED_MODEL,
                    "field_map": {"text": "content"},
                },
            )
        except Exception as exc:
            logger.error("Failed to create Pinecone index '%s': %s", INDEX_NAME, exc)
            _index = None
            return None
    else:
        logger.info("Pinecone index '%s' already exists — connecting.", INDEX_NAME)

    try:
        _index = pc.Index(INDEX_NAME)
    except Exception as exc:
        logger.error("Failed to connect to Pinecone index '%s': %s", INDEX_NAME, exc)
        _index = None

    return _index


def get_index():
    """Return the cached index handle, initializing it on first use if needed."""
    global _index
    if _index is None:
        return init_index()
    return _index


def upsert_knowledge(records: List[Dict], namespace: str = NAMESPACE) -> bool:
    """
    Ingest approved TESSA knowledge (rulebook, FAQs, procedures, tax info) into
    Pinecone. Each record must look like: {"_id": "...", "content": "..."}.
    Returns True on success, False if anything prevented the upsert.
    """
    index = get_index()
    if index is None:
        logger.error("Cannot upsert knowledge — Pinecone index is not available.")
        return False

    if not records:
        logger.warning("upsert_knowledge called with an empty records list.")
        return False

    for r in records:
        if not isinstance(r, dict) or "_id" not in r or "content" not in r:
            logger.error("Malformed record skipped — each record needs '_id' and 'content'.")
            return False
        if not str(r["content"]).strip():
            logger.error("Malformed record skipped — empty 'content' for id %r.", r.get("_id"))
            return False

    try:
        index.upsert_records(namespace=namespace, records=records)
        logger.info("Upserted %d record(s) into Pinecone namespace '%s'.", len(records), namespace)
        return True
    except Exception as exc:
        logger.error("Pinecone upsert failed: %s", exc)
        return False


def search_raw(question: str, namespace: str, top_k: int = 3) -> List[Dict]:
    """
    Search a Pinecone namespace and return each hit's FULL metadata fields
    (not just "content"). Used directly by callers that stored structured
    records (e.g. glossary entries with plain/technical definitions,
    examples, related terms) and need more than the plain-text field back.

    Returns an empty list (never raises) on any failure or "no match" -
    callers should treat that as "nothing verified found".
    """
    if not question or not question.strip():
        return []

    index = get_index()
    if index is None:
        logger.warning("Search skipped — Pinecone index is not available.")
        return []

    try:
        hits = index.search(
            namespace=namespace,
            query={
                "top_k": top_k,
                "inputs": {"text": question},
            },
        )
    except Exception as exc:
        logger.error("Pinecone search failed: %s", exc)
        return []

    try:
        if isinstance(hits, dict):
            hit_list = hits.get("result", {}).get("hits", [])
        else:
            hit_list = getattr(getattr(hits, "result", None), "hits", []) or []
    except Exception as exc:
        logger.error("Unexpected Pinecone search response shape: %s", exc)
        return []

    results: List[Dict] = []
    for h in hit_list:
        if isinstance(h, dict):
            fields = h.get("fields", {}) or {}
            hit_id = h.get("_id")
            score = h.get("_score")
        else:
            fields = getattr(h, "fields", {}) or {}
            hit_id = getattr(h, "_id", None)
            score = getattr(h, "_score", None)

        if fields:
            results.append({"id": hit_id, "score": score, "fields": dict(fields)})

    if not results:
        logger.info("No relevant Pinecone results in namespace '%s' for this query.", namespace)

    return results


def search_tessa_knowledge(question: str, top_k: int = 3, namespace: str = NAMESPACE) -> List[Dict]:
    """
    Search TESSA's Pinecone knowledge base for content relevant to `question`.

    Returns a list of {"id": str, "content": str, "score": float}. Returns an
    empty list (never raises) if there's no match, Pinecone is unreachable, or
    the index isn't configured — callers should treat an empty list as "no
    verified knowledge available" and fall back to TESSA's escalation behavior.
    """
    raw = search_raw(question, namespace=namespace, top_k=top_k)
    results = []
    for r in raw:
        content = r["fields"].get("content")
        if content:
            results.append({"id": r["id"], "content": content, "score": r["score"]})
    return results


def build_context_block(results: List[Dict]) -> str:
    """Turn search results into a plain-text context block for the LLM prompt."""
    if not results:
        return ""
    lines = [f"- {r['content']}" for r in results if r.get("content")]
    if not lines:
        return ""
    return "Retrieved IRD Grenada knowledge (verified — base your answer on this):\n" + "\n".join(lines)