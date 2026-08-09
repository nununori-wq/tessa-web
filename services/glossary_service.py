"""
TESSA's tax glossary: structured term definitions (plain + technical meaning,
example, related terms, common misunderstanding, synonyms) plus
comparison-table entries (e.g. Individual vs. Business), searchable through
the same Pinecone index as TESSA's FAQ knowledge, in a separate namespace.

Entries are stored with the term itself PLUS its synonyms folded into the
embedded "content" field, so a taxpayer asking "what does filing my taxes
mean" can land on the "Tax Return" entry without knowing the official term.

IMPORTANT: this module only searches and formats glossary content - it does
not invent it. See scripts/ingest_tessa_glossary.py for the actual entries
and the accuracy caveats attached to them.
"""

import json
import logging
from typing import Dict, List, Optional

from services.pinecone_service import search_raw, upsert_knowledge

logger = logging.getLogger("chatbot")

GLOSSARY_NAMESPACE = "glossary"

ENTRY_TYPE_TERM = "term"
ENTRY_TYPE_COMPARISON = "comparison"


def build_term_record(
    entry_id: str,
    term: str,
    plain_definition: str,
    technical_definition: str,
    example: str,
    related_terms: List[str],
    common_misunderstanding: str,
    synonyms: List[str],
    category: str = "",
    source_url: str = "",
) -> Dict:
    """
    Build a Pinecone-ready record for a single glossary term. The "content"
    field (what actually gets embedded for search) folds in the term and its
    synonyms so natural-language phrasing matches without an exact term hit.
    """
    content = (
        f"{term} ({', '.join(synonyms)}). "
        f"Plain meaning: {plain_definition} "
        f"Technical meaning: {technical_definition} "
        f"Example: {example}"
    )
    return {
        "_id": entry_id,
        "content": content,
        "entry_type": ENTRY_TYPE_TERM,
        "term": term,
        "plain_definition": plain_definition,
        "technical_definition": technical_definition,
        "example": example,
        "related_terms": ", ".join(related_terms),
        "common_misunderstanding": common_misunderstanding,
        "synonyms": ", ".join(synonyms),
        "category": category,
        "source_url": source_url,
    }


def build_comparison_record(
    entry_id: str,
    title: str,
    left_label: str,
    right_label: str,
    rows: List[Dict],
    category: str = "",
    source_url: str = "",
) -> Dict:
    """
    Build a Pinecone-ready record for a comparison entry (e.g. "Individual vs
    Business Taxpayer"). `rows` is a list of
    {"aspect": str, "left": str, "right": str}. The table is stored as a
    JSON string in a metadata field since Pinecone metadata doesn't support
    nested objects directly, and is parsed back out on retrieval.
    """
    row_text = "; ".join(f"{r['aspect']}: {r['left']} vs {r['right']}" for r in rows)
    content = f"Comparison: {title} ({left_label} vs {right_label}). {row_text}"
    return {
        "_id": entry_id,
        "content": content,
        "entry_type": ENTRY_TYPE_COMPARISON,
        "title": title,
        "left_label": left_label,
        "right_label": right_label,
        "rows_json": json.dumps(rows),
        "category": category,
        "source_url": source_url,
    }


def ingest_glossary(records: List[Dict]) -> bool:
    """Upsert glossary/comparison records into the glossary namespace."""
    return upsert_knowledge(records, namespace=GLOSSARY_NAMESPACE)


def search_glossary(question: str, top_k: int = 2) -> List[Dict]:
    """
    Search the glossary namespace and return full structured entries
    (not just the embedded content string). Returns [] on no match or if
    Pinecone is unavailable - never raises, never invents an entry.
    """
    raw_hits = search_raw(question, namespace=GLOSSARY_NAMESPACE, top_k=top_k)
    entries = []
    for hit in raw_hits:
        fields = hit.get("fields", {})
        if fields.get("entry_type") == ENTRY_TYPE_COMPARISON:
            try:
                rows = json.loads(fields.get("rows_json", "[]"))
            except (json.JSONDecodeError, TypeError):
                rows = []
            entries.append({
                "id": hit["id"],
                "score": hit["score"],
                "entry_type": ENTRY_TYPE_COMPARISON,
                "title": fields.get("title", ""),
                "left_label": fields.get("left_label", ""),
                "right_label": fields.get("right_label", ""),
                "rows": rows,
                "source_url": fields.get("source_url", ""),
            })
        else:
            entries.append({
                "id": hit["id"],
                "score": hit["score"],
                "entry_type": ENTRY_TYPE_TERM,
                "term": fields.get("term", ""),
                "plain_definition": fields.get("plain_definition", ""),
                "technical_definition": fields.get("technical_definition", ""),
                "example": fields.get("example", ""),
                "related_terms": fields.get("related_terms", ""),
                "common_misunderstanding": fields.get("common_misunderstanding", ""),
                "source_url": fields.get("source_url", ""),
            })
    return entries


def build_glossary_context(entries: List[Dict]) -> str:
    """
    Turn glossary/comparison hits into an LLM-ready context block. Term
    entries get their full plain/technical/example/related/misunderstanding
    structure; comparison entries get rendered as a ready-to-reuse markdown
    table so TESSA can drop it straight into a response when useful.
    """
    if not entries:
        return ""

    blocks = ["Retrieved glossary knowledge (verified — base definitions on this, do not add facts beyond it):"]

    for e in entries:
        if e["entry_type"] == ENTRY_TYPE_COMPARISON:
            table_lines = [
                f"\nComparison table for \"{e['title']}\" (you may reuse this markdown table directly in your answer):",
                f"| Aspect | {e['left_label']} | {e['right_label']} |",
                "|---|---|---|",
            ]
            for row in e.get("rows", []):
                table_lines.append(f"| {row.get('aspect','')} | {row.get('left','')} | {row.get('right','')} |")
            blocks.append("\n".join(table_lines))
        else:
            blocks.append(
                f"\nTerm: {e['term']}\n"
                f"Plain meaning: {e['plain_definition']}\n"
                f"Technical meaning: {e['technical_definition']}\n"
                f"Example: {e['example']}\n"
                f"Related terms: {e['related_terms']}\n"
                f"Common misunderstanding: {e['common_misunderstanding']}"
            )

    return "\n".join(blocks)
