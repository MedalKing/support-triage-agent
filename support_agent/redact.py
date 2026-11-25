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
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?\b", re.IGNORECASE)),
    ("HETU", re.compile(r"\b\d{6}[-+A-FU-Y]\d{3}[0-9A-Y]\b", re.IGNORECASE)),
    # Digit groups separated by spaces or dashes: +358 40 123 4567, +358-40-123-4567,
    # 040-123-4567, (09) 123 4567, 0442223333. Dates are excluded in Redactor._replace.
    ("PHONE", re.compile(r"(?<![\w-])(?:\+\d{1,3}[ -]?)?(?:\(0?\d{1,3}\)|0?\d{1,3})(?:[ -]?\d{2,4}){2,4}(?![\w-])")),
]
MIN_PHONE_DIGITS = 7  # shorter digit runs are quantities or ids, not phone numbers
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
MIN_IBAN_DIGITS = 10  # a real IBAN has many digits; this keeps words like "ab12 this case" out
# Only contact details go back into the draft reply. Bank accounts and identity codes never do.
RESTORABLE = {"EMAIL", "PHONE"}


@dataclass
class Redactor:
    """Keeps one mapping per ticket so the same value always gets the same placeholder."""

    forward: dict[str, str] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)

    def redact(self, text: str) -> str:
        for kind, pattern in PATTERNS:
            text = pattern.sub(lambda m, kind=kind: self._replace(kind, m.group(0)), text)
        return text

    def _replace(self, kind: str, value: str) -> str:
        digits = sum(c.isdigit() for c in value)
        if kind == "PHONE" and (digits < MIN_PHONE_DIGITS or ISO_DATE.fullmatch(value)):
            return value
        if kind == "IBAN" and digits < MIN_IBAN_DIGITS:
            return value
        return self._placeholder(kind, value)

    def _placeholder(self, kind: str, value: str) -> str:
        if value.startswith("[") and value.endswith("]"):
            return value
        if value not in self.forward:
            self.counters[kind] = self.counters.get(kind, 0) + 1
            self.forward[value] = f"[{kind}_{self.counters[kind]}]"
        return self.forward[value]

    def restore(self, text: str) -> str:
        """Put contact details back for the human reviewer. Bank accounts and identity codes stay hidden."""
        for value, placeholder in self.forward.items():
            if placeholder[1:].split("_")[0] in RESTORABLE:
                text = text.replace(placeholder, value)
        return text

    @property
    def secrets(self) -> list[str]:
        return list(self.forward)
