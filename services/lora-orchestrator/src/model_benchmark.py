"""
ModelBenchmark — evaluerer base models til valuta-arketype LoRA træning.

Måler perplexity-proxy, structured output compliance og policy adherence.
Ingen faktisk LoRA træning — det kræver GPU + trænede base models.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Decision JSON schema der evalueres mod
DECISION_SCHEMA_KEYS = {"action", "position_pct", "confidence"}

# Reasoning kvalitetsindikatorer
REASONING_KEYWORDS = {
    "numbers": ["%", "EUR", "DKK", "USD", "0.", "1.", "2.", "3.", "4.", "5."],
    "spread": ["spread", "margin"],
    "momentum": ["momentum", "trend", "bevægelse", "change", "ændring"],
    "risk": ["risiko", "stop", "max", "threshold", "grænse", "risk"],
}


class ModelBenchmark:
    """
    Benchmark-infrastruktur til at evaluere base models mod valuta-domænet.

    Brug kun modeller der faktisk svarer — skip resten uden fejl.
    """

    def __init__(self, models_config: dict[str, dict]) -> None:
        """
        models_config eksempel:
        {
            "mamba": {"url": "http://host.docker.internal:8081", "model": "mamba-gpt-7b"},
            "phi3":  {"url": "http://host.docker.internal:8082", "model": "Phi-3-mini"},
        }
        """
        self.models_config = dict(models_config)

    def run_benchmark(
        self,
        dataset_path: str,
        n_samples: int = 20,
    ) -> dict[str, Any]:
        """
        Kør benchmark mod alle tilgængelige modeller.

        Returnerer dict med per-model metrics + recommendation.
        """
        samples = self._load_samples(dataset_path, n_samples)
        if not samples:
            return {"error": "no_samples", "dataset_path": dataset_path}

        results: dict[str, Any] = {}
        for model_name, config in self.models_config.items():
            url = config.get("url", "")
            model_id = config.get("model", model_name)

            # Tjek om modellen er tilgængelig
            if not self._is_available(url):
                logger.info("ModelBenchmark: %s ikke tilgængelig — springer over", model_name)
                results[model_name] = {"available": False, "skipped": True}
                continue

            metrics = self._benchmark_model(url, model_id, samples)
            results[model_name] = {"available": True, **metrics}

        # Find bedste model
        available = {k: v for k, v in results.items() if v.get("available") and not v.get("skipped")}
        if available:
            best = max(
                available.items(),
                key=lambda kv: (
                    kv[1].get("json_valid_pct", 0) * 0.3
                    + kv[1].get("policy_compliant_pct", 0) * 0.4
                    + kv[1].get("reasoning_score", 0) * 0.3
                ),
            )
            results["recommendation"] = best[0]
            results["recommendation_score"] = round(
                best[1].get("json_valid_pct", 0) * 0.3
                + best[1].get("policy_compliant_pct", 0) * 0.4
                + best[1].get("reasoning_score", 0) * 0.3,
                3,
            )
        else:
            results["recommendation"] = None
            results["recommendation_score"] = 0.0
            results["note"] = "Ingen modeller tilgængelige"

        return results

    def save_report(
        self,
        results: dict[str, Any],
        path: str = "babyai/lora/benchmark_report.json",
    ) -> None:
        """Gem benchmark rapport til JSON fil."""
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        logger.info("ModelBenchmark: rapport gemt til %s", path)

    # ── Internal ────────────────────────────────────────────────────────────────

    def _load_samples(self, dataset_path: str, n: int) -> list[dict]:
        """Indlæs op til n eksempler fra JSONL fil."""
        path = Path(dataset_path)
        if not path.exists():
            logger.warning("ModelBenchmark: dataset ikke fundet: %s", dataset_path)
            return []
        samples = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    samples.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
                if len(samples) >= n:
                    break
        return samples

    def _is_available(self, base_url: str) -> bool:
        """Tjek om model endpoint svarer."""
        try:
            url = base_url.rstrip("/") + "/health"
            req = urllib.request.Request(url, headers={"User-Agent": "BabyAI-Benchmark/1.0"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _benchmark_model(
        self,
        base_url: str,
        model_id: str,
        samples: list[dict],
    ) -> dict[str, Any]:
        """Kør benchmark for én model mod alle samples."""
        response_times: list[float] = []
        json_valid_count = 0
        policy_compliant_count = 0
        reasoning_scores: list[float] = []

        for sample in samples:
            prompt = self._build_prompt(sample)
            start = time.monotonic()
            response_text = self._call_model(base_url, prompt, max_tokens=200)
            elapsed_ms = (time.monotonic() - start) * 1000
            response_times.append(elapsed_ms)

            if response_text is None:
                continue

            # Tjek JSON validity
            decision = self._extract_json(response_text)
            if decision is not None:
                json_valid_count += 1
                # Tjek policy compliance
                if self._is_policy_compliant(decision):
                    policy_compliant_count += 1

            # Score reasoning kvalitet
            reasoning_scores.append(self._score_reasoning(response_text))

        n = len(samples)
        avg_time = sum(response_times) / len(response_times) if response_times else 0.0
        json_pct = json_valid_count / n if n > 0 else 0.0
        policy_pct = policy_compliant_count / n if n > 0 else 0.0
        avg_reasoning = sum(reasoning_scores) / len(reasoning_scores) if reasoning_scores else 0.0

        return {
            "model_id": model_id,
            "n_samples": n,
            "avg_response_ms": round(avg_time, 1),
            "json_valid_pct": round(json_pct, 3),
            "policy_compliant_pct": round(policy_pct, 3),
            "reasoning_score": round(avg_reasoning, 3),
        }

    def _build_prompt(self, sample: dict) -> str:
        """Byg benchmark prompt fra et eksempel."""
        ctx = sample.get("context") or {}
        pair = ctx.get("pair", "USD/DKK")
        momentum = float(ctx.get("momentum_24h", 0)) * 100
        spread = float(ctx.get("spread_pct", 0)) * 100
        capital = float(ctx.get("agent_capital_eur", 1.0))
        condition = ctx.get("market_conditions", "unknown")

        return (
            f"Du er en valuta-trading agent. Analyser følgende situation og giv en beslutning.\n\n"
            f"Valutapar: {pair}\n"
            f"Momentum 24h: {momentum:+.2f}%\n"
            f"Spread: {spread:.3f}%\n"
            f"Kapital: {capital:.3f} EUR\n"
            f"Markedsforhold: {condition}\n\n"
            f"Giv reasoning (min 20 tegn) og decision som JSON med felterne: "
            f"action, position_pct (max 0.30), confidence.\n"
        )

    def _call_model(self, base_url: str, prompt: str, max_tokens: int = 200) -> str | None:
        """Send prompt til model og returner tekst-svar."""
        try:
            payload = json.dumps({
                "prompt": prompt,
                "n_predict": max_tokens,
                "temperature": 0.3,
                "stop": ["\n\n", "###"],
            }).encode("utf-8")
            url = base_url.rstrip("/") + "/completion"
            req = urllib.request.Request(
                url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "BabyAI-Benchmark/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return str(result.get("content") or result.get("text") or "")
        except Exception as exc:
            logger.debug("ModelBenchmark: model call fejlede: %s", exc)
            return None

    def _extract_json(self, text: str) -> dict | None:
        """Forsøg at parse JSON fra model output."""
        # Find JSON blok
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        try:
            parsed = json.loads(text[start:end])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        return None

    def _is_policy_compliant(self, decision: dict) -> bool:
        """Tjek om decision overholder policy (position <= 30%, reasoning >= 20 chars)."""
        position_pct = float(decision.get("position_pct", 1.0))
        if position_pct > 0.30:
            return False
        return True

    def _score_reasoning(self, text: str) -> float:
        """
        Simpel heuristik for reasoning-kvalitet.

        Scorer 0.0–1.0 baseret på:
        - Indeholder tal? (+0.25)
        - Nævner spread? (+0.25)
        - Nævner momentum? (+0.25)
        - Nævner risiko/grænser? (+0.25)
        """
        text_lower = text.lower()
        score = 0.0
        for category, keywords in REASONING_KEYWORDS.items():
            if any(kw.lower() in text_lower for kw in keywords):
                score += 0.25
        return round(score, 3)
