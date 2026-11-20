"""G2 - the input guard. Runs in the application zone before anything reaches the AI.

Strictness comes from the narrow door (signed in, entitled, on topic, schema-bound
output), not from a high rejection rate: logs and stack traces look odd, so a
suspicious ticket is *flagged* for human review, not thrown away. Only input that
cannot be processed at all (empty, far too large) is rejected.

Rule-based for now. A small classifier model can be added as one more check
without changing the callers.
"""
import re
import unicodedata
from dataclasses import dataclass, field

from shared.redaction import redact
from shared.schemas import GuardCheck

MAX_TEXT_CHARS = 20_000
MAX_DIAGNOSTICS_CHARS = 120_000
MIN_TEXT_CHARS = 10

# Zero-width, direction-control and other invisible characters used to hide instructions.
# Zero-width, bidirectional-control and soft-hyphen characters. Escapes on purpose: none of them belongs in a source file.
_INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\u00ad]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BLANK_RUN = re.compile(r"\n{4,}")

_INJECTION = [
    re.compile(p, re.I)
    for p in [
        r"\b(ignore|disregard|forget|override)\b.{0,30}\b(previous|prior|above|earlier|all|your)\b.{0,30}\b(instruction|prompt|rule|guideline)s?\b",
        r"\b(reveal|show|print|repeat|output|leak)\b.{0,30}\b(system prompt|your (instructions|prompt|rules)|hidden (prompt|instructions))",
        r"\byou are now\b",
        r"\b(act|behave|respond) as\b.{0,30}\b(unrestricted|jailbroken|uncensored|dan)\b",
        r"\b(developer|debug|god|admin) mode\b",
        r"\bjailbreak\b",
        r"</?\s*(system|assistant|instructions?|tool_result)\s*>",
        r"\[/?\s*(INST|SYSTEM)\s*\]",
        r"<\|(im_start|im_end|system|endoftext)\|>",
        r"\b(send|give|show|paste|print|dump|quote)\b.{0,30}\b(source ?code|the repository|your repo|internal (docs|documentation|design))",
        r"\bnew instructions?\s*:",
    ]
]

_CRITICAL = {
    "security": re.compile(
        r"\b(vulnerabilit(y|ies)|exploit(ed|able)?|CVE-\d{4}-\d+|security (issue|hole|bug|flaw|incident)|"
        r"unauthori[sz]ed access|privilege escalation|remote code execution|authentication bypass|data breach)\b",
        re.I,
    ),
    "data": re.compile(
        r"\b(data ?loss|lost (my |our |the )?data|corrupt(ed|ion)?|inconsistent data|wrong results?|silently? (dropp?ed|lost))\b",
        re.I,
    ),
}


@dataclass
class InputVerdict:
    decision: str  # pass | flag | reject
    text: str = ""  # normalised original, kept for operators
    redacted: str = ""  # the copy the AI gets
    diagnostics: str = ""  # redacted; the original diagnostics are not kept
    reasons: list[str] = field(default_factory=list)
    checks: list[GuardCheck] = field(default_factory=list)
    critical: str | None = None  # "security" | "data"

    @property
    def flagged(self) -> bool:
        return self.decision == "flag"


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _INVISIBLE.sub("", text)
    text = _CONTROL.sub(" ", text)
    return _BLANK_RUN.sub("\n\n\n", text).strip()


def inspect(text: str, diagnostics: str = "") -> InputVerdict:
    checks: list[GuardCheck] = []
    if len(text or "") > MAX_TEXT_CHARS or len(diagnostics or "") > MAX_DIAGNOSTICS_CHARS:
        return InputVerdict(
            "reject",
            reasons=[f"The text is too long (limit: {MAX_TEXT_CHARS:,} characters, diagnostics {MAX_DIAGNOSTICS_CHARS:,})."],
            checks=[GuardCheck("G2", "size", "block", "over the size limit")],
        )
    clean, clean_diag = normalise(text), normalise(diagnostics)
    if len(clean) < MIN_TEXT_CHARS:
        return InputVerdict(
            "reject",
            reasons=["Describe the problem in at least a sentence."],
            checks=[GuardCheck("G2", "size", "block", "too short")],
        )
    checks.append(GuardCheck("G2", "normalise", "pass", f"{len(text) - len(clean)} characters removed"))

    redacted, counts = redact(clean)
    redacted_diag, diag_counts = redact(clean_diag)
    for kind, n in diag_counts.items():
        counts[kind] = counts.get(kind, 0) + n
    detail = ", ".join(f"{n}× {kind}" for kind, n in sorted(counts.items())) or "nothing to redact"
    checks.append(GuardCheck("G2", "redact", "pass", detail))

    reasons = []
    hits = [p.pattern[:40] for p in _INJECTION if p.search(clean) or p.search(clean_diag)]
    if hits:
        reasons.append("Possible instructions to the AI in the text.")
        checks.append(GuardCheck("G2", "injection", "flag", f"{len(hits)} pattern(s) matched"))
    else:
        checks.append(GuardCheck("G2", "injection", "pass"))

    critical = next((kind for kind, pattern in _CRITICAL.items() if pattern.search(clean)), None)
    if critical:
        checks.append(GuardCheck("G2", "critical", "flag", f"mentions a {critical} problem: escalate"))

    return InputVerdict(
        decision="flag" if reasons else "pass",
        text=clean,
        redacted=redacted,
        diagnostics=redacted_diag,
        reasons=reasons,
        checks=checks,
        critical=critical,
    )
