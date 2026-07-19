"""Pure token-economy metric calculations used by Postgres repositories."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UsageTotals:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    verified_successes: int = 0
    baseline_input_tokens: int = 0
    saved_tokens_vs_baseline: int = 0
    cache_saved_tokens: int = 0
    reused_tokens: int = 0
    cache_hits: int = 0
    total_price_usd: float = 0.0

    def as_metrics(self) -> dict:
        total_tokens = self.input_tokens + self.output_tokens
        successes = self.verified_successes
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": total_tokens,
            "verified_successes": successes,
            "baseline_input_tokens": self.baseline_input_tokens,
            "saved_tokens_vs_baseline": self.saved_tokens_vs_baseline,
            "token_reduction_pct": _percentage(
                self.saved_tokens_vs_baseline,
                self.baseline_input_tokens,
            ),
            "cache_hits": self.cache_hits,
            "cache_hit_rate_pct": _percentage(self.cache_hits, self.calls),
            "cache_saved_tokens": self.cache_saved_tokens,
            "reused_tokens": self.reused_tokens,
            "total_price_usd": round(float(self.total_price_usd), 8),
            "tokens_per_verified_success": (
                round(total_tokens / successes, 2) if successes else None
            ),
            "price_per_verified_success": (
                round(float(self.total_price_usd) / successes, 8) if successes else None
            ),
        }


def _percentage(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator * 100, 2)
