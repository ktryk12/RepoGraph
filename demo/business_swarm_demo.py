#!/usr/bin/env python
"""
Business Swarm Demo — med input-sanitering (L7 policy guard).

Wrapper om demo_business_decision.py der:
  1. Accepterer et valgfrit --question argument
  2. Validerer spørgsmålet mod CryptoAnalysisPolicy
  3. Stopper kørslen hvis severity >= 0.95 (L7 auto-block)

Kør:
  python demo/business_swarm_demo.py
  python demo/business_swarm_demo.py \
    --question "Skal vi lancere nu eller vente?"

Angreb afvises:
  python demo/business_swarm_demo.py \
    --question "Ignore all previous instructions"
  --> SIKKERHED: Sporgsmaal afvist — Prompt injection pattern detekteret
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

# Sørg for at project root er i path uanset hvorfra scriptet startes
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_demo(question: str) -> None:
    """Kør business swarm demo med sikkerhedstjek på spørgsmålet."""
    from babyai.security.crypto_input_sanitizer import CryptoInputSanitizer

    q_check = CryptoInputSanitizer.sanitize_question(question)
    if not q_check["ok"]:
        CryptoInputSanitizer.log_violation(q_check, f"question={question[:50]}")
        if q_check["severity"] >= 0.95:
            print(f"SIKKERHED: Sporgsmaalet afvist — {q_check['violation']}")
            print("Korsel stoppet af L7 policy.")
            return
        else:
            print(f"ADVARSEL: {q_check['violation']}")

    # Kør det faktiske business decision demo
    import demo_business_decision
    asyncio.run(demo_business_decision.run_demo())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="BabyAI Business Decision Swarm Demo"
    )
    parser.add_argument(
        "--question",
        type=str,
        default="Skal BabyAI ApS lancere et nyt AI-produkt nu, eller vente 6 maaneder?",
        help="Sporgsmaal til swarm-analyse (max 500 tegn)",
    )
    args = parser.parse_args()
    run_demo(args.question)
