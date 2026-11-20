"""G4 - the output guard. The last check inside the enclave before anything leaves.

The wall between analyst and responder is what keeps the source in; this guard
exists to catch a mistake in building that wall. A draft that trips a *leak*
check does not leave the enclave at all: the application zone only learns that
it was blocked, and a person writes the reply.
"""
import re

from shared.corpus import Passage
from shared.redaction import find_secrets
from shared.schemas import GuardCheck

from .internal_index import InternalIndex

LEAK_CHECKS = {"secrets", "canary", "source_fingerprint", "internal_paths"}

_URL = re.compile(r"https?://[^\s)>\]]+")
_HTML = re.compile(r"<\s*(script|img|iframe|a|div|span|style|object|embed|form|input|svg)\b", re.I)
_CODE_SPAN = re.compile(r"`([^`\n]{2,120})`")
_PROMISE = re.compile(
    r"\b(we|i)('ll| will| shall| can)? (guarantee|promise)\b|\b(refund|compensation|compensate)\b|"
    r"\bwill be (fixed|released|available) (by|in|on|next|this)\b|\bETA\b|\bnext (release|version) will\b",
    re.I,
)


def check(draft: str, passages: list[Passage], issue: dict | None, internal: InternalIndex, canaries: list[str], allowed_url_prefixes: list[str]) -> list[GuardCheck]:
    if not draft.strip():
        return [GuardCheck("G4", "draft", "pass", "no draft to check")]

    checks = []

    def add(name: str, bad: bool, verdict: str, detail: str = "") -> None:
        checks.append(GuardCheck("G4", name, verdict if bad else "pass", detail if bad else ""))

    secrets = find_secrets(draft)
    add("secrets", bool(secrets), "block", ", ".join(secrets))

    hit = [c for c in canaries if c and c in draft]
    add("canary", bool(hit), "block", f"{len(hit)} canary string(s): the wall between analyst and responder has a hole")

    leaked = internal.leaked_lines(draft) if internal.enabled else 0
    add("source_fingerprint", leaked > 0, "block", f"{leaked} line(s) match the private source")

    paths = internal.paths_in(draft) if internal.enabled else []
    add("internal_paths", bool(paths), "block", f"{len(paths)} repository path(s)")

    names = internal.internal_names_in(draft) if internal.enabled else []
    add("internal_symbols", bool(names), "flag", f"{len(names)} name(s) that only exist in the private source")

    foreign = [u for u in _URL.findall(draft) if not any(u.startswith(prefix) for prefix in allowed_url_prefixes)]
    add("links", bool(foreign), "block", f"{len(foreign)} link(s) outside the documentation")

    add("format", bool(_HTML.search(draft)), "block", "contains HTML")

    allowed = "\n".join([p.text for p in passages] + [p.heading for p in passages])
    if issue:
        allowed += "\n" + " ".join(str(issue.get(k, "")) for k in ("title", "summary", "workaround", "fixed_in"))
    ungrounded = [span for span in _CODE_SPAN.findall(draft) if span not in allowed]
    add("grounding", bool(ungrounded), "flag", f"{len(ungrounded)} command(s) or option(s) not found in the cited documentation")

    add("policy", bool(_PROMISE.search(draft)), "flag", "sounds like a promise (date, fix, refund)")
    return checks


def leaks(checks: list[GuardCheck]) -> bool:
    return any(c.verdict == "block" and c.check in LEAK_CHECKS for c in checks)
