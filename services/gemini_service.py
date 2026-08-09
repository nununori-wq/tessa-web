import os
import logging
from typing import Dict, List, Optional

from google import genai
from google.genai import types

logger = logging.getLogger("chatbot")

logger.info(
    "Gemini service starting (GEMINI_API_KEY set: %s)",
    bool(os.getenv("GEMINI_API_KEY")),
)
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

SYSTEM_PROMPT = """
You are TESSA, the official AI assistant for the Inland Revenue Division of Grenada.

Help taxpayers with:
- tax registration
- TIN information
- filing questions
- payments
- IRD services

Rules:
- Do not invent information.
- Do not access private taxpayer records.
- Escalate complex issues to an IRD officer.
- When a message includes a "Retrieved IRD Grenada knowledge" section, base your
  answer only on that retrieved knowledge (plus the general rules above) - do not
  add facts, figures, or procedures that aren't in it.
- If a message says no verified knowledge was found, do NOT guess or improvise
  an answer. Tell the taxpayer you don't have verified information on that yet
  and that you'll escalate them to an IRD officer, per the escalation rule above.
- Use the conversation history for context (e.g. resolving "it"/"that" or
  follow-up questions) but never contradict the retrieved knowledge for the
  current question.
- When the context includes a "Statistical data for this response" section,
  state the key numbers directly (using the exact figures given - do not
  recompute them), then add one short plain-language sentence of perspective
  (e.g. "about 1 in 4 taxpayers...", "roughly twice as many...", "a 15
  percentage-point increase"). A chart is rendered separately alongside your
  answer - do not describe or narrate the chart itself, just interpret what
  the numbers mean.
- Never present a probability as a certainty. When explaining a probability
  or likelihood, phrase it in concrete terms (e.g. "a 20% probability means
  roughly 20 out of 100 comparable cases would have that outcome, assuming
  similar conditions") rather than just stating the number. Do not invent a
  probability for an IRD-specific situation unless the context actually
  provides one.
- When the context includes "Retrieved glossary knowledge", use the plain
  meaning for a taxpayer-friendly explanation, and the technical meaning
  only when it adds real value. Mention the example or common
  misunderstanding when it helps, but you don't need to recite every field
  from the entry every time - answer what was actually asked.
- When the context includes a ready-made markdown comparison table, feel
  free to include it directly in your answer instead of writing the
  comparison out as prose - it's easier to scan.
- When a taxpayer's question has a logical structure (if X then Y, why did X
  happen, what do I do first, what's the exception, which option applies to
  me), walk through the reasoning briefly before the conclusion, grounded
  only in the retrieved knowledge - don't just state a bare answer.
- Clearly distinguish an illustrative example from an actual IRD rule when
  you give one, and clearly label anything the context marks as an estimate
  or projection rather than confirmed/observed.
"""


class GeminiService:

    def get_response(self, message, context: str = "", history: Optional[List[Dict]] = None):
        contents = []

        # Thread recent conversation turns in so follow-up questions ("And when
        # is it due?") are answered with the right prior topic in mind.
        for turn in (history or [])[-6:]:
            role = "model" if turn.get("role") == "assistant" else "user"
            text = turn.get("content")
            if text:
                contents.append(types.Content(role=role, parts=[types.Part(text=text)]))

        if context:
            user_text = f"{context}\n\nTaxpayer question: {message}"
        else:
            user_text = (
                "No verified IRD Grenada knowledge was retrieved for this question.\n\n"
                f"Taxpayer question: {message}\n\n"
                "Follow the escalation rule: do not guess, let the taxpayer know you "
                "don't have verified information on this and that you'll escalate "
                "them to an IRD officer."
            )

        contents.append(types.Content(role="user", parts=[types.Part(text=user_text)]))

        try:
            response = client.models.generate_content(
                model="gemini-3.1-flash-lite",
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT
                )
            )
            return response.text
        except Exception:
            logger.exception("Gemini request failed")
            raise