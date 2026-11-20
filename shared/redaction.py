"""Secret and personal-data patterns.

Used on both sides of the wall: the input guard redacts what customers paste
(logs, configuration), the output guard refuses to let anything of this shape
leave in a reply. Pure Python, no Django.
"""
import re

_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|auth)\b"
    r"(\s*[:=]\s*)"
    r"(\"[^\"\n]{3,}\"|'[^'\n]{3,}'|[^\s,;\"'\]\[]{3,})"
)

SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    ("connection_string", re.compile(r"\b[a-z][a-z0-9+.-]{1,20}://[^\s:/@]+:[^\s@/]+@[^\s\"']+", re.I)),
    ("bearer_token", re.compile(r"\b(?:Bearer|Token)\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("api_key", re.compile(r"\b(?:sk|pk|rk|ghp|gho|ghs|glpat|xox[abp]|cas)[-_][A-Za-z0-9_-]{16,}\b")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
]

PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
]

_MARKER = re.compile(r"\[redacted:[a-z_]+\]")


def redact(text: str) -> tuple[str, dict[str, int]]:
    """Replace secrets and personal data with placeholders. Returns the text and a count per kind."""
    counts: dict[str, int] = {}

    def bump(kind: str) -> None:
        counts[kind] = counts.get(kind, 0) + 1

    for kind, pattern in SECRET_PATTERNS:

        def _sub(_match: re.Match, kind: str = kind) -> str:
            bump(kind)
            return f"[redacted:{kind}]"

        text = pattern.sub(_sub, text)

    def _assign(match: re.Match) -> str:
        if _MARKER.fullmatch(match.group(3)):
            return match.group(0)
        bump("secret")
        return f"{match.group(1)}{match.group(2)}[redacted:secret]"

    text = _ASSIGNMENT.sub(_assign, text)

    for kind, pattern in PII_PATTERNS:

        def _sub_pii(_match: re.Match, kind: str = kind) -> str:
            bump(kind)
            return f"[redacted:{kind}]"

        text = pattern.sub(_sub_pii, text)
    return text, counts


def find_secrets(text: str) -> list[str]:
    """Kinds of secret-shaped content found in text. Placeholders from redact() do not count."""
    found = [kind for kind, pattern in SECRET_PATTERNS if pattern.search(text)]
    for match in _ASSIGNMENT.finditer(text):
        if not _MARKER.fullmatch(match.group(3)) and not _looks_like_placeholder(match.group(3)):
            found.append("secret")
            break
    return found


def _looks_like_placeholder(value: str) -> bool:
    """Documentation writes `password = <your password>`; that is not a leak."""
    value = value.strip("\"'")
    return value.startswith(("<", "{", "$", "*", "your", "YOUR", "xxx", "XXX", "...")) or set(value) <= {"*", "x", "X", "."}
