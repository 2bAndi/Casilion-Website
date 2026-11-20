"""The contract between the application zone and the AI enclave.

Both sides import this module; nothing else is shared. The application zone
validates every result again on arrival: the enclave is trusted to try, not to
be right.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

CLASSES = (
    "usage_error",
    "configuration",
    "known_issue",
    "needs_info",
    "suspected_bug",
    "security_suspected",
    "doc_gap",
    "out_of_scope",
    "abuse",
)
ANSWER_CLASSES = ("usage_error", "configuration", "known_issue")
BUG_CLASSES = ("suspected_bug", "security_suspected")
CONFIDENCE = ("low", "medium", "high")

# Public component list. The analyst may only name one of these across the bridge.
COMPONENTS = (
    "install",
    "configuration",
    "storage",
    "query",
    "replication",
    "client",
    "licensing",
    "performance",
    "diagnostics",
    "other",
)

# What the support AI may ask the customer's side for. Codes cross the bridge, the text is fixed.
MISSING_INFO = {
    "product_version": "The exact Cassilion version and build (output of `cassilion --version`).",
    "os": "Operating system and version of the affected machine.",
    "config": "The relevant part of the configuration file, with secrets removed.",
    "logs": "The log lines around the time of the problem.",
    "diag_bundle": "The diagnostics bundle (output of `cassilion diag --json`).",
    "repro_steps": "The exact steps or statements that lead to the problem.",
    "error_message": "The complete error message as shown.",
}

MAX_DRAFT_CHARS = 6000
MAX_SUMMARY_CHARS = 2000
# A pointer names a place in the source. It never carries code.
POINTER_RE = re.compile(r"^[\w./+-]{1,200}(:\d{1,7}(-\d{1,7})?)?(@[0-9a-zA-Z._-]{1,64})?$")
VERDICTS = ("pass", "flag", "block")


@dataclass
class BridgeRecord:
    """All that passes from the analyst to the responder. No free text, no source."""

    classification: str
    confidence: str
    component: str = "other"
    public_refs: list[str] = field(default_factory=list)
    missing_info: list[str] = field(default_factory=list)
    known_issue_id: str | None = None

    def errors(self, known_passage_ids: set[str] | None = None, known_issue_ids: set[str] | None = None) -> list[str]:
        problems = []
        if self.classification not in CLASSES:
            problems.append(f"unknown classification: {self.classification!r}")
        if self.confidence not in CONFIDENCE:
            problems.append(f"unknown confidence: {self.confidence!r}")
        if self.component not in COMPONENTS:
            problems.append(f"unknown component: {self.component!r}")
        for code in self.missing_info:
            if code not in MISSING_INFO:
                problems.append(f"unknown missing-info code: {code!r}")
        if known_passage_ids is not None:
            for ref in self.public_refs:
                if ref not in known_passage_ids:
                    problems.append(f"reference is not a public passage: {ref!r}")
        if self.known_issue_id and known_issue_ids is not None and self.known_issue_id not in known_issue_ids:
            problems.append(f"unknown known issue: {self.known_issue_id!r}")
        return problems

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Finding(BridgeRecord):
    """The analyst's full result. Summary and pointers are for operators only."""

    summary: str = ""
    source_pointers: list[str] = field(default_factory=list)

    def bridge(self) -> BridgeRecord:
        return BridgeRecord(
            classification=self.classification,
            confidence=self.confidence,
            component=self.component,
            public_refs=list(self.public_refs),
            missing_info=list(self.missing_info),
            known_issue_id=self.known_issue_id,
        )


@dataclass
class GuardCheck:
    stage: str  # G1..G5
    check: str
    verdict: str  # pass | flag | block
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def validate_result(data: dict, known_passage_ids: set[str], known_issue_ids: set[str]) -> tuple[dict, list[str]]:
    """Check a result posted by the AI worker. Returns a cleaned copy and the list of problems.

    Anything malformed is a problem, never silently repaired into something sendable.
    """
    problems: list[str] = []
    raw = data.get("finding") or {}
    finding = Finding(
        classification=str(raw.get("classification", "")),
        confidence=str(raw.get("confidence", "")),
        component=str(raw.get("component", "other")),
        public_refs=[str(r) for r in (raw.get("public_refs") or [])][:12],
        missing_info=[str(m) for m in (raw.get("missing_info") or [])][:8],
        known_issue_id=(str(raw["known_issue_id"]) if raw.get("known_issue_id") else None),
        summary=str(raw.get("summary", ""))[:MAX_SUMMARY_CHARS],
        source_pointers=[str(p) for p in (raw.get("source_pointers") or [])][:40],
    )
    problems += finding.errors(known_passage_ids, known_issue_ids)
    for pointer in finding.source_pointers:
        if not POINTER_RE.match(pointer):
            problems.append("a source pointer is not of the form path:line@commit")
            finding.source_pointers = [p for p in finding.source_pointers if POINTER_RE.match(p)]
            break

    draft = data.get("draft") or ""
    if not isinstance(draft, str):
        problems.append("draft is not text")
        draft = ""
    if len(draft) > MAX_DRAFT_CHARS:
        problems.append("draft is longer than allowed")
        draft = draft[:MAX_DRAFT_CHARS]

    checks = []
    for item in (data.get("guard") or [])[:60]:
        verdict = str(item.get("verdict", ""))
        if verdict not in VERDICTS:
            problems.append("a guard verdict is malformed")
            continue
        checks.append(
            GuardCheck(
                stage=str(item.get("stage", ""))[:2],
                check=str(item.get("check", ""))[:60],
                verdict=verdict,
                detail=str(item.get("detail", ""))[:300],
            ).to_dict()
        )

    usage_raw = data.get("usage") or {}
    usage = {
        "backend": str(usage_raw.get("backend", ""))[:40],
        "model": str(usage_raw.get("model", ""))[:120],
        "prompt_version": str(usage_raw.get("prompt_version", ""))[:40],
        "tokens_in": _as_int(usage_raw.get("tokens_in")),
        "tokens_out": _as_int(usage_raw.get("tokens_out")),
        "duration_ms": _as_int(usage_raw.get("duration_ms")),
    }
    return {"finding": asdict(finding), "draft": draft, "guard": checks, "usage": usage}, problems


def _as_int(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
