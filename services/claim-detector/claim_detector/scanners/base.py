"""Abstract base scanner — all platform scanners implement this."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from claim_detector.models import ClaimCandidate


class BaseScanner(ABC):
    @property
    @abstractmethod
    def platform(self) -> str:
        """Platform identifier string."""

    @abstractmethod
    def scan(self, *, limit: int = 50) -> List[ClaimCandidate]:
        """
        Scan platform for fresh claim candidates.
        Returns at most `limit` candidates.
        Must never raise — return [] on error and log.
        """
