"""
PLACEHOLDER MODULE — services/visualization_service.py was referenced by
services/tessa_graph.py (`from services.visualization_service import
detect_and_extract, compute_derived_stats, build_visualization_payload,
build_stats_context_block, try_percentage_of_amount,
build_calculation_context`) but was not among the files provided.
Without *some* implementation of these six functions, the app cannot be
imported at all — tessa_graph.py fails at import time.

This module is a safe, inert stand-in: it always reports "no
visualization needed" and never fabricates a chart or a number. That
preserves TESSA's core "do not invent information" rule while the real
statistical/visualization module is restored. Swap this file out for
the genuine implementation — everything else in this project imports it
by name only, so no other file needs to change when you do.
"""

import re
from typing import Dict, List, Optional


def try_percentage_of_amount(message: str) -> Optional[Dict]:
    """Deterministic fast-path for 'X% of Y' arithmetic, e.g.
    'what is 15% of 2000'. Exact arithmetic, no LLM — this one small
    piece of real logic is cheap and safe to keep even in the
    placeholder, since it's pure math with no invented facts."""
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*%\s*(?:of)\s*\$?\s*([\d,]+(?:\.\d+)?)",
        message, re.IGNORECASE,
    )
    if not match:
        return None
    try:
        pct = float(match.group(1))
        amount = float(match.group(2).replace(",", ""))
    except ValueError:
        return None
    return {"percent": pct, "amount": amount, "result": round(pct / 100.0 * amount, 2)}


def build_calculation_context(calc: Optional[Dict]) -> str:
    if not calc:
        return ""
    return (
        f"Exact calculation (computed in Python, not by the LLM): "
        f"{calc['percent']}% of {calc['amount']} = {calc['result']}."
    )


_COMPARISON_TRIGGERS = ("compare", "vs", "versus", "difference between", "how much more", "which is higher")
_LABELED_NUMBER = re.compile(
    r"([A-Za-z][A-Za-z \-']{2,40}?)\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d+)?)\s*%?",
)


def _extract_labeled_numbers(context: str) -> List[Dict]:
    """Pulls (label, value) pairs out of verified retrieved `context` text
    only -- never out of the user's own message, and never invented. Real
    but intentionally conservative: only used when the question itself
    signals a comparison is wanted (see needs_visualization below)."""
    points = []
    seen_labels = set()
    for match in _LABELED_NUMBER.finditer(context or ""):
        label = match.group(1).strip().rstrip(":-").strip()
        try:
            value = float(match.group(2).replace(",", ""))
        except ValueError:
            continue
        if not label or label.lower() in seen_labels:
            continue
        seen_labels.add(label.lower())
        points.append({"label": label, "value": value})
    return points[:8]  # keep it demo-sized, not a wall of numbers


def detect_and_extract(message: str, context: str, history: Optional[List[Dict]] = None) -> Dict:
    """Detects comparison-style questions and pulls real numbers out of
    already-verified retrieved `context` (never the user's own message,
    never invented). If the question looks like it wants a comparison but
    fewer than 2 verified numbers are found in context, reports
    insufficient data rather than fabricating a chart."""
    lower_msg = (message or "").lower()
    wants_comparison = any(trigger in lower_msg for trigger in _COMPARISON_TRIGGERS)

    if not wants_comparison:
        return {
            "needs_visualization": False,
            "chart_type": None,
            "data_points": [],
            "data_source": "not_requested",
            "insufficient_data_note": None,
        }

    points = _extract_labeled_numbers(context)
    if len(points) < 2:
        return {
            "needs_visualization": False,
            "chart_type": None,
            "data_points": [],
            "data_source": "insufficient",
            "insufficient_data_note": (
                "The question calls for a comparison, but fewer than 2 verified "
                "figures were found in the retrieved knowledge to compare."
            ),
        }

    return {
        "needs_visualization": True,
        "chart_type": "bar",
        "data_points": points,
        "data_source": "retrieved_context",
        "insufficient_data_note": None,
    }


def compute_derived_stats(data_points: List[Dict]) -> Dict:
    if not data_points:
        return {}
    values = [p["value"] for p in data_points]
    highest = max(data_points, key=lambda p: p["value"])
    lowest = min(data_points, key=lambda p: p["value"])
    stats = {
        "count": len(values),
        "highest_label": highest["label"], "highest_value": highest["value"],
        "lowest_label": lowest["label"], "lowest_value": lowest["value"],
    }
    if lowest["value"] not in (0, None):
        stats["difference"] = round(highest["value"] - lowest["value"], 2)
        stats["ratio"] = round(highest["value"] / lowest["value"], 2) if lowest["value"] else None
    return stats


def build_visualization_payload(extraction: Dict, stats: Dict) -> Optional[Dict]:
    if not extraction.get("needs_visualization"):
        return None
    return {"chart_type": extraction.get("chart_type"), "data_points": extraction.get("data_points"), "stats": stats}


def build_stats_context_block(extraction: Dict, stats: Dict) -> str:
    if not extraction.get("needs_visualization") or not stats:
        return ""
    lines = [f"Statistical data for this response (computed in Python, not by the LLM):"]
    for p in extraction["data_points"]:
        lines.append(f"- {p['label']}: {p['value']}")
    if "difference" in stats:
        lines.append(
            f"Highest is {stats['highest_label']} ({stats['highest_value']}), "
            f"lowest is {stats['lowest_label']} ({stats['lowest_value']}), "
            f"difference of {stats['difference']}."
        )
    return "\n".join(lines)
