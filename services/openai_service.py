import os
from openai import OpenAI

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)


class OpenAIService:

    def get_response(self, message):

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": """
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
"""
                },
                {
                    "role": "user",
                    "content": message
                }
            ]
        )

        return response.choices[0].message.content