# =========================================================
# MeetIQ - AI Meeting Summarizer
# Local AI using Ollama
# =========================================================

import json
import requests


OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL_NAME = "llama3.2:3b"


def generate_summary(transcript):
    """
    Generate a structured AI-powered meeting summary
    using a local Ollama model.
    """

    if not transcript or not transcript.strip():
        return {
            "discussion_points": [],
            "decisions": [],
            "action_items": []
        }

    prompt = f"""
You are an AI meeting assistant for MeetIQ.

Analyze the following meeting transcript.

Return ONLY valid JSON in exactly this structure:

{{
    "discussion_points": [
        "point 1",
        "point 2"
    ],
    "decisions": [
        "decision 1",
        "decision 2"
    ],
    "action_items": [
        "action item 1",
        "action item 2"
    ]
}}

Rules:
- Do not invent information.
- Only use information present in the transcript.
- Keep each point concise.
- If there are no decisions, return an empty list.
- If there are no action items, return an empty list.
- Do not include markdown.
- Do not include explanations outside the JSON.

MEETING TRANSCRIPT:
{transcript}
"""

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL_NAME,
            "prompt": prompt,
            "stream": False
        },
        timeout=120
    )

    response.raise_for_status()

    response_data = response.json()

    response_text = response_data.get("response", "").strip()

    try:
        summary = json.loads(response_text)

    except json.JSONDecodeError:

        print("AI returned non-JSON response:")
        print(response_text)

        return {
            "discussion_points": [],
            "decisions": [],
            "action_items": []
        }

    return summary


# =========================================================
# TEST
# =========================================================

if __name__ == "__main__":

    test_transcript = """
    Today we discussed the development progress of the MeetIQ
    virtual meeting platform.

    The team completed the WebRTC meeting functionality,
    screen sharing, attention monitoring, and Whisper
    speech-to-text integration.

    The team decided to implement AI meeting summarization next.

    Keerthi will work on the summarization module.
    The team will test the feature with real meeting transcripts.
    """

    result = generate_summary(test_transcript)

    print("\nAI MEETING SUMMARY")
    print("===================")

    print("\nDISCUSSION POINTS:")
    for point in result["discussion_points"]:
        print("-", point)

    print("\nDECISIONS:")
    for decision in result["decisions"]:
        print("-", decision)

    print("\nACTION ITEMS:")
    for item in result["action_items"]:
        print("-", item)