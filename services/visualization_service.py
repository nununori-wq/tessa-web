"""
Visual statistical reasoning for TESSA.

Detects when a taxpayer's question calls for quantitative reasoning,
extracts whatever real numbers are available (from the taxpayer's own
message or from verified RAG knowledge - NEVER invented), computes derived
statistics (percentage-point change vs. relative percent change, ratios,
shares of a total, trend direction) in plain Python rather than trusting an
LLM to do arithmetic, and builds a structured payload the frontend renders
as an actual chart - not a text description of one.

If a question genuinely calls for a comparison but there isn't real,
verified numeric data behind it, this reports that plainly (data_source =
"insufficient") instead of fabricating a chart. Given TESSA's current
knowledge base is procedural FAQ content rather than statistical data, this
is the common case unless the taxpayer supplies numbers directly.
"""

import json
import logging
import re
from typing import Dict, List, Optional, TypedDict

from google.genai import types

from services.gemini_service import client

logger = logging.getLogger("chatbot")

# Matches things like "15% of my $2,000 income", "20% of 1500", "what's 7.5% of EC$800"
_PERCENT_OF_AMOUNT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*%\s*of\s*(?:EC\$|US\$|\$)?\s*(\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)


def try_percentage_of_amount(message: str) -> Optional[Dict]:
    """
    Deterministic (non-LLM) handler for the very common "how much is X% of
    Y" pattern, e.g. "15% of my $2,000 income". Regex + plain arithmetic,
    so there's zero risk of an LLM arithmetic slip on the most frequent
    numeric question TESSA will see. Returns None if the message doesn't
    match this specific pattern - callers should fall back to the general
    LLM-based extraction below for anything else.
    """
    if not message:
        return None

    match = _PERCENT_OF_AMOUNT_RE.search(message)
    if not match:
        return None

    try:
        percentage = float(match.group(1))
        amount = float(match.group(2).replace(",", ""))
    except ValueError:
        return None

    result = round((percentage / 100) * amount, 2)
    return {"percentage": percentage, "amount": amount, "result": result}


BAR_CHART = "bar"
LINE_CHART = "line"
PIE_CHART = "pie"
SCATTER_PLOT = "scatter"
PROBABILITY_VISUAL = "probability"
TABLE = "table"
NONE_VIZ = "none"

VALID_CHART_TYPES = {BAR_CHART, LINE_CHART, PIE_CHART, SCATTER_PLOT, PROBABILITY_VISUAL, TABLE, NONE_VIZ}

EXTRACTION_MODEL = "gemini-3.1-flash-lite"

_EXTRACTION_SYSTEM_PROMPT = f"""
You are TESSA's statistical-reasoning detector for the IRD Grenada assistant.
Given a taxpayer's question, the conversation so far, and any verified
knowledge retrieved for this question, decide whether the question calls for
quantitative/statistical reasoning (percentages, probabilities, proportions,
averages, rates, counts, trends over time, comparisons between categories,
growth/decline, distributions, correlation, rankings, survey results) where
a chart would help - as opposed to a simple factual, procedural, or
conversational question.

Return ONLY a JSON object (no markdown, no commentary) with these exact keys:

- "needs_visualization": true only if a chart would meaningfully improve
  understanding. False for greetings, simple procedural questions ("how do
  I register"), yes/no questions, or anything with no real quantitative
  component. A single number with nothing to compare it to usually does NOT
  need a chart.
- "data_source": one of "user_provided" (the numbers are in the taxpayer's
  own message), "rag_knowledge" (the numbers are in the verified knowledge
  provided below), or "insufficient" (the question calls for a comparison
  but there isn't real, verified numeric data available to build one).
- "chart_type": one of {sorted(VALID_CHART_TYPES)}. Use "bar" for category
  comparisons/rankings/counts, "line" for trends over time, "pie" for
  part-to-whole with few categories, "scatter" only for a real relationship
  between two numeric variables, "probability" for explaining a probability
  or chance, "table" when exact values matter more than shape. "none" if
  needs_visualization is false or data is insufficient.
- "title": a short chart title, or null.
- "unit": the unit of the values, e.g. "%", "EC$", "count" - or null.
- "data_points": a list of objects like {{"label": str, "value": number}} -
  ONLY include values explicitly present in the taxpayer's message or the
  verified knowledge. NEVER estimate, round unrealistically, or invent a
  data point that isn't actually stated. Empty list if none.
- "is_estimate": true if the source material explicitly labels these figures
  as estimates or projections, false if stated as observed/actual.
- "insufficient_data_note": if data_source is "insufficient", a short plain
  explanation of what numeric information is missing - otherwise null.

Rules:
- Never invent, estimate, or infer a numeric value not explicitly stated.
- Do not confuse a percentage with a percentage-point change.
- Do not imply a probability is a certainty.
"""


class VizExtraction(TypedDict):
    needs_visualization: bool
    data_source: str
    chart_type: str
    title: Optional[str]
    unit: Optional[str]
    data_points: List[Dict]
    is_estimate: bool
    insufficient_data_note: Optional[str]


def _fallback() -> VizExtraction:
    return {
        "needs_visualization": False,
        "data_source": "insufficient",
        "chart_type": NONE_VIZ,
        "title": None,
        "unit": None,
        "data_points": [],
        "is_estimate": False,
        "insufficient_data_note": None,
    }


def detect_and_extract(message: str, context: str = "", history: Optional[List[Dict]] = None) -> VizExtraction:
    """
    Decide whether this question needs a visualization and, if so, pull out
    whatever real numbers are available. Falls back to "no visualization" on
    any failure - a hiccup here never fabricates data as a side effect.
    """
    if not message or not message.strip():
        return _fallback()

    history_text = ""
    if history:
        lines = [f"{t.get('role', 'user')}: {t.get('content', '')}" for t in history[-4:] if t.get("content")]
        history_text = "\n".join(lines)

    prompt = (
        f"Conversation so far:\n{history_text or '(none)'}\n\n"
        f"Taxpayer's question: {message}\n\n"
        f"Verified knowledge retrieved for this question:\n{context or '(none retrieved)'}"
    )

    try:
        response = client.models.generate_content(
            model=EXTRACTION_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_EXTRACTION_SYSTEM_PROMPT,
                response_mime_type="application/json",
            ),
        )
        parsed = json.loads(response.text or "{}")
    except Exception as exc:
        logger.warning("Visualization detection failed, skipping chart: %s", exc)
        return _fallback()

    chart_type = parsed.get("chart_type")
    if chart_type not in VALID_CHART_TYPES:
        chart_type = NONE_VIZ

    raw_points = parsed.get("data_points") or []
    clean_points: List[Dict] = []
    for dp in raw_points:
        try:
            label = str(dp.get("label"))
            value = float(dp.get("value"))
            clean_points.append({"label": label, "value": value})
        except (TypeError, ValueError, AttributeError):
            continue  # drop anything malformed rather than guess at it

    needs_viz = bool(parsed.get("needs_visualization", False)) and chart_type != NONE_VIZ and len(clean_points) >= 1

    return {
        "needs_visualization": needs_viz,
        "data_source": parsed.get("data_source", "insufficient"),
        "chart_type": chart_type,
        "title": parsed.get("title"),
        "unit": parsed.get("unit"),
        "data_points": clean_points,
        "is_estimate": bool(parsed.get("is_estimate", False)),
        "insufficient_data_note": parsed.get("insufficient_data_note"),
    }


def compute_derived_stats(data_points: List[Dict]) -> Dict:
    """
    Compute derived comparisons in plain Python so the arithmetic is exact,
    rather than trusting an LLM to do it. Only handles the well-defined
    cases (two-point comparisons, shares of a total, simple trend direction)
    - anything more complex is left as raw numbers for the response text
    rather than a computed claim that might be wrong.
    """
    stats: Dict = {}

    if len(data_points) == 2:
        a, b = data_points[0]["value"], data_points[1]["value"]
        stats["difference"] = round(b - a, 4)
        stats["direction"] = "increase" if b > a else ("decrease" if b < a else "no change")
        if a != 0:
            stats["ratio"] = round(b / a, 4)
            stats["percent_change"] = round(((b - a) / a) * 100, 4)  # relative %, distinct from a pp change

    if data_points:
        total = sum(dp["value"] for dp in data_points)
        if total > 0:
            stats["shares_of_total"] = [
                {"label": dp["label"], "share_pct": round((dp["value"] / total) * 100, 2)}
                for dp in data_points
            ]
        stats["max"] = max(data_points, key=lambda d: d["value"])
        stats["min"] = min(data_points, key=lambda d: d["value"])

    if len(data_points) > 2:
        values = [dp["value"] for dp in data_points]
        stats["trend"] = (
            "increasing" if values == sorted(values) else
            "decreasing" if values == sorted(values, reverse=True) else
            "mixed"
        )

    return stats


def build_calculation_context(calc: Optional[Dict]) -> str:
    """Plain-text context block for a deterministic percentage-of-amount calculation."""
    if not calc:
        return ""
    return (
        "Statistical calculation for this response (verified — state this exact result, do not recompute it):\n"
        f"- {calc['percentage']}% of {calc['amount']} = {calc['result']}"
    )


def build_visualization_payload(extraction: VizExtraction, stats: Dict) -> Optional[Dict]:
    """Build the structured payload the frontend renders as an actual chart."""
    if not extraction["needs_visualization"] or not extraction["data_points"]:
        return None

    return {
        "type": extraction["chart_type"],
        "title": extraction["title"] or "Comparison",
        "data": extraction["data_points"],
        "unit": extraction["unit"] or "",
        "source": extraction["data_source"],
        "is_estimate": extraction["is_estimate"],
        "computed_stats": stats,
    }


def build_stats_context_block(extraction: VizExtraction, stats: Dict) -> str:
    """
    A plain-text block of the verified numbers plus Python-computed derived
    stats, appended to the LLM's context so the final response states real,
    pre-computed figures - the LLM phrases them, it doesn't calculate them.
    """
    if not extraction["data_points"]:
        return ""

    unit = extraction["unit"] or ""
    lines = ["Statistical data for this response (verified - state these exact figures, do not recompute or alter them):"]
    for dp in extraction["data_points"]:
        lines.append(f"- {dp['label']}: {dp['value']}{unit}")

    if extraction.get("is_estimate"):
        lines.append("Note: these figures are estimates/projections, not confirmed observed data - label them as such.")

    if "difference" in stats:
        lines.append(
            f"- Change from first to second value: {stats['difference']}{unit} "
            f"({stats['direction']}). If these are percentages, describe this "
            f"as a percentage-POINT change, not a percentage change."
        )
    if "percent_change" in stats:
        lines.append(f"- Relative percent change: {stats['percent_change']}%")
    if "ratio" in stats:
        lines.append(f"- Ratio of second value to first: {stats['ratio']}x")
    if "shares_of_total" in stats:
        for s in stats["shares_of_total"]:
            lines.append(f"- {s['label']} is {s['share_pct']}% of the total shown")
    if "trend" in stats:
        lines.append(f"- Overall trend across the values shown: {stats['trend']}")

    return "\n".join(lines)
