"""Conservative secret redaction for recalled notes."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RedactionResult:
    """Redacted text and the number of matched secret shapes."""

    text: str
    redaction_count: int


class SecretRedactor:
    """Remove common credential shapes without treating ordinary words as secrets."""

    _private_key = re.compile(
        r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----.*?"
        r"-----END(?: [A-Z0-9]+)? PRIVATE KEY-----",
        re.DOTALL,
    )
    _bearer = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
    _credential_url = re.compile(
        r"(?P<scheme>\b[a-z][a-z0-9+.-]*://)[^\s/:@]+:[^\s/@]+@",
        re.IGNORECASE,
    )
    _known_token = re.compile(
        r"(?<![A-Za-z0-9_-])(?:"
        r"sk-[A-Za-z0-9_-]{16,}|"
        r"gh[pousr]_[A-Za-z0-9]{20,}|"
        r"github_pat_[A-Za-z0-9_]{20,}|"
        r"AKIA[0-9A-Z]{16}|"
        r"AIza[0-9A-Za-z_-]{35}|"
        r"xox[baprs]-[A-Za-z0-9-]{10,}"
        r")(?![A-Za-z0-9_-])"
    )
    _assignment = re.compile(
        r"(?im)^(?P<prefix>\s*(?:[-*]\s+)?(?:export\s+)?"
        r"[A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|"
        r"PRIVATE[_-]?KEY|ACCESS[_-]?KEY)[A-Z0-9_]*\s*[:=]\s*)"
        r"(?P<value>[^\r\n#]+)"
    )

    def redact(self, text: str) -> RedactionResult:
        count = 0

        text, matches = self._private_key.subn("[REDACTED PRIVATE KEY]", text)
        count += matches
        text, matches = self._bearer.subn("Bearer [REDACTED]", text)
        count += matches
        text, matches = self._credential_url.subn(
            lambda match: f"{match.group('scheme')}[REDACTED]@", text
        )
        count += matches
        text, matches = self._known_token.subn("[REDACTED TOKEN]", text)
        count += matches
        text, matches = self._assignment.subn(
            lambda match: f"{match.group('prefix')}[REDACTED]", text
        )
        count += matches
        return RedactionResult(text=text, redaction_count=count)
