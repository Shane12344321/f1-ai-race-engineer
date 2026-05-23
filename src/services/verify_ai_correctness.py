"""
AI Correctness and Grounding Verification Tool.

This script runs test suites on simulated race telemetry events, submits them
to the IBM Granite LLM model, and programmatically evaluates if the generated F1
team radio output is correct, grounded, and follows structural/broadcast rules.

Usage:
  python src/services/verify_ai_correctness.py
"""

import os
import re
import sys
from typing import Dict, Any, List

# Ensure project root is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from src.services.granite_client import GraniteClient

# Define mock test events
MOCK_EVENTS = [
    {
        "name": "Race Start (Tyre Data Available)",
        "prompt": (
            "LIGHTS OUT — RACE START (Lap 1/58):\n"
            "- Total cars: 20\n"
            "- Starting tyre strategies: 18 on Soft, 2 on Medium\n"
            "Set the scene for the start of this race in an exciting "
            "broadcast style. Mention the tyre strategies in play. "
            "Do NOT mention specific driver positions or who is leading — "
            "the grid order is not yet confirmed."
        ),
        "expected_drivers": [],
        "disallowed_drivers": ["VER", "HAM", "NOR", "LEC", "PIA"],
        "required_keywords": ["soft", "medium"]
    },
    {
        "name": "Race Start (Tyre Data Unknown)",
        "prompt": (
            "LIGHTS OUT — RACE START (Lap 1/58):\n"
            "- Total cars: 20\n"
            "Set the scene for the start of this race in an exciting "
            "broadcast style. Tyre compound data is not yet available, "
            "so do not speculate about tyre strategies. "
            "Do NOT mention specific driver positions or who is leading — "
            "the grid order is not yet confirmed."
        ),
        "expected_drivers": [],
        "disallowed_drivers": ["VER", "HAM", "NOR", "LEC", "PIA"],
        "required_keywords": []
    },
    {
        "name": "Overtake Battle",
        "prompt": (
            "DRIVERS IN THIS EVENT: HAM, NOR\n"
            "OVERTAKE on Lap 12/58 (Race time: 00:18:45):\n"
            "- HAM has moved from P3 to P2\n"
            "- Overtook: NOR\n"
            "- HAM: 312 km/h on Medium tyres\n"
            "Describe this overtake dynamically based on the speeds and tyres. "
            "Only reference the drivers listed above."
        ),
        "expected_drivers": ["HAM", "NOR"],
        "disallowed_drivers": ["VER", "LEC"],
        "required_keywords": ["overtake", "p2", "medium"]
    },
    {
        "name": "Pit Stop Strategy",
        "prompt": (
            "DRIVERS IN THIS EVENT: VER\n"
            "PIT STOP on Lap 18/58 (Race time: 00:27:12):\n"
            "- VER (P1) has entered the pits\n"
            "- Current tyres: Soft (17 laps old)\n"
            "Describe this pit stop based on their tyre age. "
            "Only reference the driver listed above."
        ),
        "expected_drivers": ["VER"],
        "disallowed_drivers": ["HAM", "NOR", "LEC"],
        "required_keywords": ["pit", "soft", "lap 18"]
    },
    {
        "name": "Safety Car Phase",
        "prompt": (
            "DRIVERS IN THIS EVENT: LEC\n"
            "TRACK STATUS CHANGE on Lap 22/58 (Race time: 00:33:15):\n"
            "- New status: Safety Car deployed\n"
            "- Race leader: LEC\n"
            "Describe this track status change and its immediate effect on the pace "
            "of the leader LEC. "
            "Only reference the driver listed above."
        ),
        "expected_drivers": ["LEC"],
        "disallowed_drivers": ["VER", "HAM"],
        "required_keywords": ["safety car", "leader", "lap 22"]
    },
    {
        "name": "Driver Retirement (DNF)",
        "prompt": (
            "DRIVERS IN THIS EVENT: ALO\n"
            "RETIREMENT (DNF) on Lap 34/58 (Race time: 00:52:10):\n"
            "- ALO has retired from the race (was running in P6)\n"
            "- Last tyre compound run: Hard\n"
            "Describe this retirement and its impact on their race. "
            "Only reference the driver listed above."
        ),
        "expected_drivers": ["ALO"],
        "disallowed_drivers": ["VER", "HAM", "NOR", "LEC"],
        "required_keywords": ["retired", "p6", "hard"]
    }
]

DRIVER_NAMES_MAP = {
    "VER": ["VER", "Verstappen"],
    "HAM": ["HAM", "Hamilton"],
    "NOR": ["NOR", "Norris"],
    "LEC": ["LEC", "Leclerc"],
    "PIA": ["PIA", "Piastri"],
    "ALO": ["ALO", "Alonso"],
    "SAI": ["SAI", "Sainz"],
    "RUS": ["RUS", "Russell"],
}

def verify_response(response: str, event: Dict[str, Any]) -> List[str]:
    """
    Programmatically verify responses against structural and factual rules.
    """
    errors = []

    # 1. Non-empty check
    if not response or len(response.strip()) < 10:
        errors.append("Response is too short or empty.")
        return errors

    # 2. Refusal check
    refusal_patterns = ["i cannot", "i can't", "as an ai", "as a language model", "access to real-time"]
    for pattern in refusal_patterns:
        if pattern in response.lower():
            errors.append(f"Model returned a refusal pattern: '{pattern}'")

    # 3. Plain text check (No markdown or asterisks)
    if "**" in response or "##" in response or "*" in response or "```" in response:
        errors.append("Response contains forbidden markdown formatting (bold, headers, asterisks).")

    # 4. Emojis check
    if any(ord(char) > 127999 for char in response):
        errors.append("Response contains emojis or non-text symbols.")

    # 5. Sentence count check (broadcast rules require 1 to 4 sentences)
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', response) if s.strip()]
    if len(sentences) < 1 or len(sentences) > 4:
        errors.append(f"Response has {len(sentences)} sentences (expected between 1 and 4).")

    # 6. Factual Grounding: Expected drivers check
    if event["expected_drivers"]:
        any_driver_mentioned = False
        for code in event["expected_drivers"]:
            aliases = DRIVER_NAMES_MAP.get(code, [code])
            for alias in aliases:
                pattern = rf"\b{re.escape(alias)}\b"
                if re.search(pattern, response, re.IGNORECASE):
                    any_driver_mentioned = True
                    # Warn if full name was used instead of code
                    if alias != code:
                        print(f"    [Warning] Model expanded code '{code}' to full name '{alias}'.")
    
        if not any_driver_mentioned:
            errors.append(f"None of the involved drivers ({', '.join(event['expected_drivers'])}) were mentioned.")

    # 7. Hallucination Guard: Disallowed drivers check (should not hallucinate non-involved drivers)
    for code in event["disallowed_drivers"]:
        aliases = DRIVER_NAMES_MAP.get(code, [code])
        for alias in aliases:
            pattern = rf"\b{re.escape(alias)}\b"
            if re.search(pattern, response, re.IGNORECASE):
                errors.append(f"Hallucination Warning: Driver '{alias}' mentioned but not in event telemetry data.")
                break

    return errors

def main():
    print("==================================================")
    print(" F1 Race Engineer — AI Correctness Evaluator      ")
    print("==================================================")

    # Initialize client
    client = GraniteClient()
    if not client.is_available:
        print(f"\n❌ Error: GraniteClient initialization failed: {client.error_message}")
        print("Please configure your .env file with valid credentials.")
        sys.exit(1)

    print("\n✓ GraniteClient loaded successfully. Starting evaluation...\n")

    total_tests = len(MOCK_EVENTS)
    passed_tests = 0

    for i, event in enumerate(MOCK_EVENTS, 1):
        print(f"Test {i}/{total_tests}: {event['name']}")
        print(f"  Prompt sent: {event['prompt'].splitlines()[0]}...")
        
        # Call model
        response = client.generate(event["prompt"])
        
        print(f"  AI Narration: \"{response}\"")
        
        # Run verification checks
        errors = verify_response(response, event)
        
        if not errors:
            print("  Result: ✅ PASSED (Grounded, factual, correct format)\n")
            passed_tests += 1
        else:
            print("  Result: ❌ FAILED")
            for err in errors:
                print(f"    - {err}")
            print()

    print("==================================================")
    print(f" Evaluation Complete: {passed_tests}/{total_tests} passed.")
    print("==================================================")
    
    if passed_tests == total_tests:
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == "__main__":
    main()
