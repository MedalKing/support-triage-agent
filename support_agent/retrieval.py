"""Retrieval over the runbooks with BM25.

Deliberately small and dependency-free: the runbook set is tiny, and BM25 is a strong,
explainable baseline. Swapping in embeddings would change only this module.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

RUNBOOK_DIR = Path(__file__).parent / "data" / "runbooks"
_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


@dataclass(frozen=True)
class Doc:
    doc_id: str
    title: str
    text: str


class BM25Index:
    def __init__(self, docs: list[Doc], k1: float = 1.5, b: float = 0.75) -> None:
        self.docs = docs
        self.k1, self.b = k1, b
        self.tfs = [Counter(tokenize(d.text)) for d in docs]
        self.lengths = [sum(tf.values()) for tf in self.tfs]
        self.avg_len = sum(self.lengths) / len(docs)
        df = Counter(term for tf in self.tfs for term in tf)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, k: int = 2) -> list[tuple[Doc, float]]:
        terms = tokenize(query)
        scored = []
        for doc, tf, length in zip(self.docs, self.tfs, self.lengths):
            score = 0.0
            for t in terms:
                if t not in tf:
                    continue
                f = tf[t]
                score += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * length / self.avg_len))
            scored.append((doc, score))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [pair for pair in scored[:k] if pair[1] > 0]


def load_runbooks(directory: Path = RUNBOOK_DIR) -> BM25Index:
    docs = []
    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        title = text.splitlines()[0].lstrip("# ").strip()
        docs.append(Doc(doc_id=path.stem, title=title, text=text))
    return BM25Index(docs)
