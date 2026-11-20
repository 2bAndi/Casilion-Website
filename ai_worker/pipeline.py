"""The pipeline inside the AI enclave: retrieve -> analyst -> bridge (G3) -> responder -> output guard (G4)."""
import logging
import time
from dataclasses import asdict

from shared.corpus import Corpus, load_known_issues
from shared.schemas import GuardCheck

from . import output_guard
from .analyst import analyse, ticket_text
from .config import Config
from .internal_index import InternalIndex
from .llm import OpenAICompatible, Usage
from .prompts import PROMPT_VERSION
from .responder import respond

log = logging.getLogger("ai_worker")

WITHHELD = "[withheld: this text contained source code or a secret; see the pointers instead]"


class Pipeline:
    def __init__(self, config: Config):
        self.config = config
        self.corpus = Corpus.load(config.public_dir)
        self.issues = load_known_issues(config.known_issues_file)
        public_text = "\n".join(p.text for p in self.corpus.passages)
        self.internal = InternalIndex(config.repo_dir, public_text)
        self.llm = None
        if config.backend == "openai":
            self.llm = OpenAICompatible(config.llm_base_url, config.llm_model, config.llm_api_key, config.llm_timeout)
        log.info(
            "pipeline ready: %d public passages, %d known issues, internal corpus %s, backend %s",
            len(self.corpus.passages),
            len(self.issues),
            f"{len(self.internal.files)} files" if self.internal.enabled else "not configured",
            f"{config.backend} ({config.llm_model})" if self.llm else "stub (rules, no model)",
        )

    def run(self, job: dict) -> dict:
        started = time.monotonic()
        ticket = job["ticket"]
        docs_base_url = job.get("docs_base_url", "")
        usage = Usage()

        # Stage 1 - analyst: public documentation and private source.
        finding, used = analyse(ticket, self.corpus, self.issues, self.internal, self.llm)
        usage.add(used)

        # G3 - bridge: only enums and public document ids pass. No summary, no pointers, no free text.
        bridge = finding.bridge()
        problems = bridge.errors(self.corpus.passage_ids, {i["id"] for i in self.issues})
        checks = [GuardCheck("G3", "bridge_schema", "block" if problems else "pass", "; ".join(problems)[:300])]

        # Stage 2 - responder: public passages only.
        draft = ""
        if not problems:
            text, _diagnostics = ticket_text(ticket)
            draft, used = respond(text, ticket.get("language", "en"), bridge, self.corpus, self.issues, docs_base_url, self.llm)
            usage.add(used)

        # G4 - output guard.
        passages = [p for p in (self.corpus.get(ref) for ref in bridge.public_refs) if p]
        issue = next((i for i in self.issues if i["id"] == bridge.known_issue_id), None)
        guard = output_guard.check(draft, passages, issue, self.internal, self.config.canaries, [docs_base_url] if docs_base_url else [])
        checks += guard
        if output_guard.leaks(guard):
            draft = ""  # a leaking draft never leaves the enclave

        # The summary goes to operators through the application zone. It must not carry source either.
        if finding.summary and (self.internal.leaked_lines(finding.summary) or any(c in finding.summary for c in self.config.canaries)):
            finding.summary = WITHHELD
            checks.append(GuardCheck("G4", "summary_fingerprint", "flag", "the analyst's summary quoted source text and was withheld"))

        return {
            "finding": asdict(finding),
            "draft": draft,
            "guard": [c.to_dict() for c in checks],
            "usage": {
                "backend": self.config.backend,
                "model": self.config.llm_model if self.llm else "rules",
                "prompt_version": PROMPT_VERSION,
                "tokens_in": usage.tokens_in,
                "tokens_out": usage.tokens_out,
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        }
