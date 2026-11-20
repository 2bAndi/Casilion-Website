"""The public knowledge corpus: Markdown files split into passages with stable IDs.

The same loader serves the documentation pages, the MCP knowledge tools and the
AI worker. Search is plain BM25 over words; good enough to exercise the flow, and
replaceable by an embedding index behind the same three methods.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

_WORD = re.compile(r"[a-z0-9_]+")
_HEADING = re.compile(r"^(#{1,3})\s+(.*\S)\s*$")
_STOP = frozenset(
    "a an and are as at be but by can do does for from has have how i if in is it its my not of on or our "
    "so that the their then there these this to was we what when where which while why will with you your".split()
)


def slugify(text: str, _sep: str = "-") -> str:
    """Heading anchor. Also handed to the Markdown renderer so links and passage IDs agree."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "section"


def tokenize(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1]


@dataclass(frozen=True)
class Passage:
    id: str  # "<doc>#<anchor>"
    doc: str
    doc_title: str
    heading: str
    text: str

    @property
    def anchor(self) -> str:
        return self.id.split("#", 1)[1]

    def url(self, docs_base_url: str) -> str:
        return f"{docs_base_url.rstrip('/')}/{self.doc}/#{self.anchor}"

    def excerpt(self, limit: int = 420) -> str:
        text = " ".join(self.text.split())
        return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


@dataclass(frozen=True)
class Doc:
    slug: str
    title: str
    summary: str
    markdown: str


class Corpus:
    def __init__(self, docs: list[Doc], passages: list[Passage]):
        self.docs = {d.slug: d for d in docs}
        self.passages = passages
        self._by_id = {p.id: p for p in passages}
        self._tokens = [tokenize(f"{p.doc_title} {p.heading} {p.heading} {p.text}") for p in passages]
        self._df: dict[str, int] = {}
        for tokens in self._tokens:
            for term in set(tokens):
                self._df[term] = self._df.get(term, 0) + 1
        self._avg_len = (sum(len(t) for t in self._tokens) / len(self._tokens)) if self._tokens else 0.0

    @classmethod
    def load(cls, directory: Path | str) -> "Corpus":
        docs, passages = [], []
        for path in sorted(Path(directory).glob("*.md")):
            markdown = path.read_text(encoding="utf-8")
            slug = path.stem
            title, summary = slug.replace("-", " ").title(), ""
            heading, anchor, buffer = "", "top", []
            seen: dict[str, int] = {}

            def flush() -> None:
                text = "\n".join(buffer).strip()
                if text:
                    passages.append(Passage(f"{slug}#{anchor}", slug, title, heading or title, text))

            for line in markdown.splitlines():
                match = _HEADING.match(line)
                if match and match.group(1) == "#" and not heading and not buffer:
                    title = match.group(2)
                    continue
                if match:
                    flush()
                    heading = match.group(2)
                    base = slugify(heading)
                    seen[base] = seen.get(base, 0) + 1
                    anchor = base if seen[base] == 1 else f"{base}_{seen[base] - 1}"
                    buffer = []
                    continue
                if line.lstrip().startswith(">"):
                    continue  # notices and asides are shown on the page but are not answer material
                if not summary and line.strip() and not line.startswith(("#", "`", "|", "-")):
                    summary = line.strip()
                buffer.append(line)
            flush()
            docs.append(Doc(slug, title, summary, markdown))
        return cls(docs, passages)

    @property
    def passage_ids(self) -> set[str]:
        return set(self._by_id)

    def get(self, passage_id: str) -> Passage | None:
        return self._by_id.get(passage_id)

    def search(self, query: str, limit: int = 5) -> list[tuple[Passage, float]]:
        terms = tokenize(query)
        if not terms or not self.passages:
            return []
        n, k1, b = len(self.passages), 1.4, 0.75
        scored = []
        for passage, tokens in zip(self.passages, self._tokens):
            if not tokens:
                continue
            score = 0.0
            for term in set(terms):
                tf = tokens.count(term)
                if not tf:
                    continue
                idf = math.log(1 + (n - self._df[term] + 0.5) / (self._df[term] + 0.5))
                score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(tokens) / self._avg_len))
            if score > 0:
                scored.append((passage, round(score, 3)))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]


def load_known_issues(path: Path | str) -> list[dict]:
    """Known issues are public: id, title, versions, fixed_in, summary, workaround, doc_ref."""
    path = Path(path)
    if not path.exists():
        return []
    return list(json.loads(path.read_text(encoding="utf-8")))


def known_issues_for(issues: list[dict], version: str | None) -> list[dict]:
    if not version:
        return issues
    return [i for i in issues if not i.get("versions") or version in i["versions"]]
