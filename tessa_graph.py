"""
TESSA's LangGraph orchestration workflow.

Wires language detection, intent understanding, knowledge retrieval,
statistical/visualization detection, response generation, and safety
checking into a single graph, so each stage is independently testable and
future stages (real multilingual translation, cross-session memory,
ticketing, channel-specific formatting) can be dropped in without touching
the others.

Current build focuses on: intent detection + entity extraction +
follow-up-aware query rewriting (services/intent_service.py), visual
statistical reasoning (services/visualization_service.py), and a searchable
tax glossary with comparison-table support (services/glossary_service.py).
Language detection and channel formatting remain pass-through stubs - the
graph's shape already matches TESSA's target architecture, so those are just
the next nodes to fill in.

Graph shape:

    detect_language -> detect_intent -> [needs_clarification?]
                                            |no                |yes
                                            v                  v
                                    retrieve_knowledge       clarify
                                            |                   |
                                            v                   |
                                    retrieve_glossary            |
                                            |                   |
                                            v                   |
                                  visualization_decision         |
                                            |                   |
                                            v                   |
                                    generate_response            |
                                            |                   |
                                            v                   |
                                      safety_check                |
                                            |                   |
                                            v                   v
                                        format_channel  <--------+
                                            |
                                            v
                                           END
"""

import logging
from typing import Dict, List, Optional, TypedDict

from langgraph.graph import StateGraph, END

from services.intent_service import detect_intent, IntentResult
from services.pinecone_service import search_tessa_knowledge, build_context_block
from services.glossary_service import search_glossary, build_glossary_context
from services.visualization_service import (
    detect_and_extract,
    compute_derived_stats,
    build_visualization_payload,
    build_stats_context_block,
    try_percentage_of_amount,
    build_calculation_context,
)
from services.gemini_service import GeminiService

logger = logging.getLogger("chatbot")

gemini_service = GeminiService()


class TessaState(TypedDict, total=False):
    # Input
    message: str
    history: List[Dict]
    channel: str

    # Language detection (stub for now - always "en")
    language: str

    # Intent detection / context resolution
    intent: str
    entities: Dict
    needs_clarification: bool
    clarification_question: Optional[str]
    search_query: str

    # RAG retrieval
    knowledge_hits: List[Dict]
    glossary_hits: List[Dict]
    context: str

    # Statistical / visualization reasoning
    visualization_payload: Optional[Dict]
    stats_context: str
    visualization_note: Optional[str]

    # Output
    escalate: bool
    response: str


def detect_language_node(state: TessaState) -> TessaState:
    # Stub: TESSA currently operates in English only. This node exists so the
    # graph's shape already matches the target multilingual architecture -
    # swap it for real language detection + response translation later
    # without touching any other node.
    state["language"] = "en"
    return state


def detect_intent_node(state: TessaState) -> TessaState:
    result: IntentResult = detect_intent(state["message"], state.get("history"))
    state["intent"] = result["intent"]
    state["entities"] = result["entities"]
    state["needs_clarification"] = result["needs_clarification"]
    state["clarification_question"] = result["clarification_question"]
    state["search_query"] = result["search_query"]
    logger.info(
        "Intent detected: %s | needs_clarification=%s | rewritten query=%r",
        result["intent"], result["needs_clarification"], result["search_query"],
    )
    return state


def route_after_intent(state: TessaState) -> str:
    return "clarify" if state.get("needs_clarification") else "retrieve"


def clarification_node(state: TessaState) -> TessaState:
    state["response"] = state.get("clarification_question") or (
        "Could you tell me a bit more about what you need help with?"
    )
    state["knowledge_hits"] = []
    state["glossary_hits"] = []
    state["escalate"] = False
    return state


def retrieve_knowledge_node(state: TessaState) -> TessaState:
    query = state.get("search_query") or state["message"]
    hits = search_tessa_knowledge(query)
    state["knowledge_hits"] = hits
    state["context"] = build_context_block(hits)
    return state


def retrieve_glossary_node(state: TessaState) -> TessaState:
    """
    Looks up matching glossary terms / comparison tables for this question -
    runs alongside FAQ retrieval so a definitional question ("what does
    taxable income mean?") or a comparison question ("individual vs
    business?") gets grounded even when it doesn't match a procedural FAQ.
    """
    query = state.get("search_query") or state["message"]
    hits = search_glossary(query)
    state["glossary_hits"] = hits

    glossary_context = build_glossary_context(hits)
    if glossary_context:
        existing = state.get("context", "")
        state["context"] = f"{existing}\n\n{glossary_context}" if existing else glossary_context

    return state


def visualization_decision_node(state: TessaState) -> TessaState:
    """
    Decides whether this question needs quantitative reasoning, and if so,
    whether there's real verified data to visualize. Never fabricates data:
    if the LLM extraction finds nothing solid, no chart is produced and
    generate_response is told to explain what's missing instead.
    """
    # Deterministic fast path for the very common "X% of Y" calculation -
    # exact arithmetic, no LLM involved, so it's never wrong.
    calc = try_percentage_of_amount(state["message"])
    calc_context = build_calculation_context(calc)

    extraction = detect_and_extract(state["message"], state.get("context", ""), state.get("history"))

    stats = compute_derived_stats(extraction["data_points"]) if extraction["needs_visualization"] else {}
    payload = build_visualization_payload(extraction, stats)

    state["visualization_payload"] = payload
    stats_block = build_stats_context_block(extraction, stats) if payload else ""
    state["stats_context"] = "\n\n".join(b for b in [calc_context, stats_block] if b)
    state["visualization_note"] = (
        extraction.get("insufficient_data_note") if extraction.get("data_source") == "insufficient" else None
    )

    logger.info(
        "Visualization decision: needs_viz=%s chart_type=%s data_points=%d source=%s calc_matched=%s",
        extraction["needs_visualization"], extraction["chart_type"],
        len(extraction["data_points"]), extraction["data_source"], bool(calc),
    )
    return state


def generate_response_node(state: TessaState) -> TessaState:
    context = state.get("context", "")
    stats_context = state.get("stats_context", "")
    if stats_context:
        context = f"{context}\n\n{stats_context}" if context else stats_context

    if state.get("visualization_note"):
        note = state["visualization_note"]
        extra = (
            f"The taxpayer's question calls for a data comparison, but there "
            f"isn't enough verified numeric data available to show one. "
            f"Explain plainly what's missing rather than guessing: {note}"
        )
        context = f"{context}\n\n{extra}" if context else extra

    response = gemini_service.get_response(
        state["message"],
        context=context,
        history=state.get("history"),
    )
    state["response"] = response
    return state


def safety_check_node(state: TessaState) -> TessaState:
    # Do not invent information: if nothing verified was retrieved from
    # either the FAQ/rules knowledge or the glossary, this is an escalation
    # case even though generate_response already produced a (should-be)
    # escalation-style answer per its own instructions - this flag lets
    # callers (e.g. the /chat route, future ticketing) act on it too. This
    # also serves as the workflow's "Accuracy Check" step for statistical
    # answers - the numbers themselves were already computed in Python
    # (visualization_service), not by the LLM, so there's nothing further to
    # re-verify here beyond the knowledge check.
    knowledge_hits = state.get("knowledge_hits") or []
    glossary_hits = state.get("glossary_hits") or []
    state["escalate"] = len(knowledge_hits) == 0 and len(glossary_hits) == 0
    if state["escalate"]:
        logger.info("Safety check: no verified knowledge or glossary hits - escalation applies.")
    return state


def format_channel_node(state: TessaState) -> TessaState:
    # Stub: web chat wants plain markdown text, which is already what
    # generate_response produces. Voice/email/SMS formatting is the next
    # phase - add a per-channel pass here without touching the rest of the
    # graph (e.g. re-run the response through a "make this SMS-length" pass).
    channel = state.get("channel", "web")
    if channel != "web":
        logger.info("Channel formatting for '%s' not implemented yet - returning web format.", channel)
    return state


def build_tessa_graph():
    graph = StateGraph(TessaState)

    graph.add_node("detect_language", detect_language_node)
    graph.add_node("detect_intent", detect_intent_node)
    graph.add_node("clarify", clarification_node)
    graph.add_node("retrieve_knowledge", retrieve_knowledge_node)
    graph.add_node("retrieve_glossary", retrieve_glossary_node)
    graph.add_node("visualization_decision", visualization_decision_node)
    graph.add_node("generate_response", generate_response_node)
    graph.add_node("safety_check", safety_check_node)
    graph.add_node("format_channel", format_channel_node)

    graph.set_entry_point("detect_language")
    graph.add_edge("detect_language", "detect_intent")

    graph.add_conditional_edges(
        "detect_intent",
        route_after_intent,
        {"clarify": "clarify", "retrieve": "retrieve_knowledge"},
    )

    graph.add_edge("clarify", "format_channel")
    graph.add_edge("retrieve_knowledge", "retrieve_glossary")
    graph.add_edge("retrieve_glossary", "visualization_decision")
    graph.add_edge("visualization_decision", "generate_response")
    graph.add_edge("generate_response", "safety_check")
    graph.add_edge("safety_check", "format_channel")
    graph.add_edge("format_channel", END)

    return graph.compile()


tessa_graph = build_tessa_graph()


def run_tessa(message: str, history: Optional[List[Dict]] = None, channel: str = "web") -> Dict:
    """
    Entry point for the FastAPI layer: runs TESSA's full graph for one
    taxpayer message and returns the final state (response, intent,
    entities, escalate flag, knowledge_hits, etc.).
    """
    initial_state: TessaState = {
        "message": message,
        "history": history or [],
        "channel": channel,
    }
    return tessa_graph.invoke(initial_state)