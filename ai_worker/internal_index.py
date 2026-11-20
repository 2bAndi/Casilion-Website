"""The internal corpus: a read-only snapshot of the private product repository.

It exists only inside the AI enclave. The analyst searches it; the output guard
uses the same index to recognise source text, file paths and internal names in
anything that is about to leave. Without PRODUCT_REPO_DIR the index is empty and
the analyst works from the public documentation alone.
"""
import re
import zlib
from pathlib import Path

_EXTENSIONS = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".inl", ".ipp", ".cmake", ".md", ".txt", ".proto", ".py", ".sh"}
_SKIP_DIRS = {".git", "build", "out", "dist", "node_modules", "third_party", "vendor", ".venv", "__pycache__"}
_MAX_FILE_BYTES = 1_000_000
_MIN_LINE = 25

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{5,}")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
_SPACE = re.compile(r"\s+")


def _code_shaped(name: str) -> bool:
    """snake_case, camelCase or names with digits: unlikely to be ordinary prose."""
    return "_" in name.strip("_") or any(c.isdigit() for c in name) or sum(c.isupper() for c in name[1:]) >= 1 and not name.isupper()


def _fingerprint(line: str) -> int | None:
    normalised = _SPACE.sub("", line)
    return zlib.crc32(normalised.encode("utf-8", "ignore")) if len(normalised) >= _MIN_LINE else None


class InternalIndex:
    def __init__(self, repo_dir: Path | None, public_text: str = ""):
        self.files: dict[str, list[str]] = {}
        self.commit = ""
        self._fingerprints: set[int] = set()
        self._identifiers: set[str] = set()
        if repo_dir and repo_dir.is_dir():
            self._scan(repo_dir)
            public_words = set(_IDENTIFIER.findall(public_text))
            self._identifiers -= public_words
            # A line that also stands in the public documentation is not a leak.
            for line in public_text.splitlines():
                self._fingerprints.discard(_fingerprint(line))

    @property
    def enabled(self) -> bool:
        return bool(self.files)

    def _scan(self, root: Path) -> None:
        self.commit = _read_commit(root)
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in _EXTENSIONS:
                continue
            if _SKIP_DIRS & set(path.relative_to(root).parts[:-1]):
                continue
            try:
                if path.stat().st_size > _MAX_FILE_BYTES:
                    continue
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            relative = path.relative_to(root).as_posix()
            self.files[relative] = lines
            for line in lines:
                mark = _fingerprint(line)
                if mark is not None:
                    self._fingerprints.add(mark)
                self._identifiers.update(name for name in _IDENTIFIER.findall(line) if _code_shaped(name))

    # ---- for the analyst -----------------------------------------------------------

    def pointer(self, path: str, line: int) -> str:
        return f"{path}:{line}" + (f"@{self.commit[:12]}" if self.commit else "")

    def search(self, text: str, limit: int = 8) -> list[dict]:
        """Lines of the snapshot that share the most distinctive words with the text."""
        terms = {w.lower() for w in _WORD.findall(text) if len(w) >= 5}
        if not terms or not self.files:
            return []
        hits = []
        for path, lines in self.files.items():
            for number, line in enumerate(lines, 1):
                lowered = line.lower()
                score = sum(1 for term in terms if term in lowered)
                if score >= 2:
                    hits.append((score, path, number))
        hits.sort(key=lambda h: (-h[0], h[1], h[2]))
        results = []
        for _score, path, number in hits[:limit]:
            lines = self.files[path]
            snippet = "\n".join(lines[max(0, number - 4) : number + 6])
            results.append({"pointer": self.pointer(path, number), "snippet": snippet})
        return results

    # ---- for the output guard -------------------------------------------------------

    def leaked_lines(self, text: str) -> int:
        return sum(1 for line in text.splitlines() if _fingerprint(line) in self._fingerprints)

    def paths_in(self, text: str) -> list[str]:
        return sorted({path for path in self.files if "/" in path and path in text})[:5]

    def internal_names_in(self, text: str) -> list[str]:
        return sorted({name for name in _IDENTIFIER.findall(text) if name in self._identifiers})[:8]


def _read_commit(root: Path) -> str:
    head = root / ".git" / "HEAD"
    try:
        content = head.read_text(encoding="utf-8").strip()
        if content.startswith("ref:"):
            return (root / ".git" / content.split(" ", 1)[1]).read_text(encoding="utf-8").strip()
        return content
    except OSError:
        return ""
