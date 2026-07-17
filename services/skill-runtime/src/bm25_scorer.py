"""
babyai/skills/bm25_scorer.py — Standalone BM25 scorer for skill candidate ranking.

Standalone — does NOT import from repobrain to avoid coupling.
Tokenization mirrors shared/babyai_shared/repobrain/index_bm25.py (_TOKEN_RE).

Public API:
    scorer = BM25Scorer()
    score  = scorer.score("trading", "trading forex arbitrage")          # single doc
    scores = scorer.score_many("trading", ["doc1 text", "doc2 text"])    # corpus IDF
"""
from __future__ import annotations

import math
import re
from typing import List

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]{2,}")

# Minimum score to accept a skill candidate — mirrors SkillCrawler._MIN_ACCEPT_SCORE
_MIN_ACCEPT_SCORE: float = 0.70


def _tokenize(text: str) -> List[str]:
    """Tokenize text: lowercase alphanum tokens ≥2 chars, underscore-split expanded."""
    tokens = _TOKEN_RE.findall(text.lower())
    expanded: List[str] = []
    for tok in tokens:
        expanded.append(tok)
        if "_" in tok:
            expanded.extend(part for part in tok.split("_") if len(part) > 1)
    return expanded


class BM25Scorer:
    """
    BM25 scorer for single documents and corpora.

    Args:
        k1: term frequency saturation parameter (default 1.5)
        b:  length normalisation parameter (default 0.75)
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = float(k1)
        self.b  = float(b)

    # ── Public API ────────────────────────────────────────────────────────────

    def score(self, query: str, document: str) -> float:
        """
        BM25 score of query against a single document.

        Treats the document as a 1-document corpus (IDF = log(2)).
        Returns float capped at 1.0.
        """
        return self.score_many(query, [document])[0]

    def score_many(self, query: str, documents: List[str]) -> List[float]:
        """
        BM25 scores for query against a corpus of documents.

        IDF is computed across the full corpus so relative scores are meaningful.
        Each score is normalised to [0.0, 1.0] by dividing by the max raw score
        (or 1.0 if all scores are zero).

        Returns list of floats in the same order as documents.
        """
        if not documents:
            return []

        q_tokens = _tokenize(query)
        if not q_tokens:
            return [0.0] * len(documents)

        # Tokenise all docs once
        tokenised = [_tokenize(d) for d in documents]
        doc_lens  = [len(t) for t in tokenised]
        avg_len   = sum(doc_lens) / max(1, len(doc_lens))
        total_docs = len(documents)

        # Build TF per doc and DF across corpus
        tfs: List[dict] = []
        df:  dict[str, int] = {}
        for tokens in tokenised:
            tf: dict[str, int] = {}
            for tok in tokens:
                tf[tok] = tf.get(tok, 0) + 1
            for tok in tf:
                df[tok] = df.get(tok, 0) + 1
            tfs.append(tf)

        # Compute raw BM25 scores
        raw_scores = [0.0] * total_docs
        for tok in q_tokens:
            tok_df = df.get(tok, 0)
            if tok_df == 0:
                continue
            idf = math.log(1.0 + (total_docs - tok_df + 0.5) / (tok_df + 0.5))
            for i, tf in enumerate(tfs):
                freq = tf.get(tok, 0)
                if freq == 0:
                    continue
                dl    = doc_lens[i]
                denom = freq + self.k1 * (1.0 - self.b + self.b * dl / max(1.0, avg_len))
                raw_scores[i] += idf * (freq * (self.k1 + 1.0)) / denom

        # Normalise: divide by max, cap at 1.0
        max_score = max(raw_scores) if raw_scores else 0.0
        if max_score <= 0.0:
            return [0.0] * total_docs
        return [min(1.0, s / max_score) for s in raw_scores]
