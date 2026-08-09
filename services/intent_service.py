"""
Intent detection, entity extraction, and follow-up query rewriting for TESSA.

This is the "Intent Detection" stage of TESSA's LangGraph workflow, and it
also does double duty as "Context/Memory Retrieval": it uses recent
conversation history to resolve follow-up questions like "What about
businesses?" or "And when is it due?" into a standalone question that RAG
retrieval can actually search on, since Pinecone has no idea what "that"
refers to on its own.

This module never answers the taxpayer's question itself - it only
classifies and rewrites it. Answering happens later in the graph, grounded in
whatever the RAG step retrieves.
"""

import json
import logging
from typing import Dict, List, Optional, TypedDict

from google.genai import types

from services.gemini_service import client  # reuse the already-configured Gemini client

logger = logging.getLogger("chatbot")

INTENT_CATEGORIES = [
    "registration",
    "tin",
    "filing",
    "payment",
    "tax_clearance",
    "gct",
    "property_tax",
    "stamp_tax",
    "vehicle_licence",
    "refund",
    "audit_or_appeal",
    "general_inquiry",
    "escalation_request",
    "other",
]

INTENT_MODEL = "gemini-3.1-flash-lite"

_INTENT_SYSTEM_PROMPT = f"""
You are the intent- and entity-extraction module for TESSA, the IRD Grenada
taxpayer assistant. Given the taxpayer's latest message and recent
conversation history, return ONLY a JSON object (no markdown, no commentary,
no extra keys) with exactly these keys:

- "intent": one of {INTENT_CATEGORIES}
- "entities": an object with any of these keys you can confidently identify -
  "tax_type", "taxpayer_type", "business_type", "dates", "amounts", "forms" -
  omit keys you can't identify. Values should be short strings or lists of strings.
- "needs_clarification": true only if the message is genuinely too ambiguous
  to answer or search for without asking the taxpayer something first.
- "clarification_question": a short question to ask the taxpayer if
  needs_clarification is true, otherwise null.
- "search_query": a standalone, self-contained version of the taxpayer's
  question that resolves any references to prior turns. For example, if the
  prior topic was individual TIN registration and the taxpayer now asks
  "What about businesses?", search_query should be "How do businesses
  register for a TIN?" - specific and complete on its own, with no dangling
  pronouns like "that" or "it".

This task is only about understanding the question, not answering it - do
not invent tax rules or facts here.
"""


class IntentResult(TypedDict):
    intent: str
    entities: Dict
    needs_clarification: bool
    clarification_question: Optional[str]
    search_query: str


def _history_to_text(history: List[Dict]) -> str:
    if not history:
        return "(no prior messages)"
    lines = []
    for turn in history[-6:]:  # last few turns is enough context for follow-ups
        role = turn.get("role", "user")
        content = turn.get("content", "")
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else "(no prior messages)"


def detect_intent(message: str, history: Optional[List[Dict]] = None) -> IntentResult:
    """
    Classify intent, extract entities, and rewrite the message into a
    standalone search query using recent conversation history.

    Falls back to a safe default (raw message as the query, "general_inquiry"
    intent, no clarification) if the LLM call or JSON parsing fails, so a
    hiccup here never breaks the chat flow - TESSA just searches on the raw
    message instead of a rewritten one.
    """
    fallback: IntentResult = {
        "intent": "general_inquiry",
        "entities": {},
        "needs_clarification": False,
        "clarification_question": None,
        "search_query": message,
    }

    if not message or not message.strip():
        return fallback

    history_text = _history_to_text(history or [])
    prompt = (
        f"Conversation so far:\n{history_text}\n\n"
        f"Taxpayer's latest message: {message}"
    )

    try:
        response = client.models.generate_content(
            model=INTENT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_INTENT_SYSTEM_PROMPT,
                response_mime_type="application/json",
            ),
        )
        raw = response.text or ""
        parsed = json.loads(raw)
    except Exception as exc:
        logger.warning("Intent detection failed, falling back to raw message: %s", exc)
        return fallback

    intent = parsed.get("intent")
    if intent not in INTENT_CATEGORIES:
        intent = "general_inquiry"

    search_query = parsed.get("search_query") or message

    return {
        "intent": intent,
        "entities": parsed.get("entities") or {},
        "needs_clarification": bool(parsed.get("needs_clarification", False)),
        "clarification_question": parsed.get("clarification_question"),
        "search_query": search_query,
    }
