"""Pseudonymise personal data before it reaches the model.

Emails, phone numbers, IBANs and Finnish personal identity codes are replaced with stable
placeholders such as [EMAIL_1]. The mapping stays on our side, so a draft reply can be
restored for the human reviewer without the model ever seeing the real values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Order matters: the most specific patterns run first so a phone pattern can't eat an IBAN.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?\b")),
    ("HETU", re.compile(r"\b\d{6}[-+A-FU-Y]\d{3}[0-9A-Y]\b")),
    ("PHONE", re.compile(r"(?<![\w-])(?:\+\d{1,3}[ -]?)?\(?0?\d{2,3}\)?[ -]?\d{3}[ -]?\d{3,4}\b")),
]


@dataclass
class Redactor:
    """Keeps one mapping per ticket so the same value always gets the same placeholder."""

    forward: dict[str, str] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)

    def redact(self, text: str) -> str:
        for kind, pattern in PATTERNS:
            text = pattern.sub(lambda m, kind=kind: self._placeholder(kind, m.group(0)), text)
        return text

    def _placeholder(self, kind: str, value: str) -> str:
        if value.startswith("[") and value.endswith("]"):
            return value
        if value not in self.forward:
            self.counters[kind] = self.counters.get(kind, 0) + 1
            self.forward[value] = f"[{kind}_{self.counters[kind]}]"
        return self.forward[value]

    def restore(self, text: str) -> str:
        for value, placeholder in self.forward.items():
            text = text.replace(placeholder, value)
        return text

    @property
    def secrets(self) -> list[str]:
        return list(self.forward)
