"""Access to the public knowledge corpus from the application zone (docs pages, MCP tools, validation)."""
from functools import lru_cache
from pathlib import Path

from django.conf import settings

from shared.corpus import Corpus, load_known_issues


def _stamp(path: Path) -> float:
    files = list(path.glob("*.md")) if path.is_dir() else [path]
    return max((f.stat().st_mtime for f in files if f.exists()), default=0.0) + len(files)


@lru_cache(maxsize=2)
def _corpus(directory: str, _stamp_value: float) -> Corpus:
    return Corpus.load(directory)


@lru_cache(maxsize=2)
def _issues(path: str, _stamp_value: float) -> tuple:
    return tuple(load_known_issues(path))


def corpus() -> Corpus:
    """Reloaded automatically when a documentation file changes."""
    directory = Path(settings.CASSILION["KNOWLEDGE_PUBLIC_DIR"])
    return _corpus(str(directory), _stamp(directory))


def known_issues() -> list[dict]:
    path = Path(settings.CASSILION["KNOWN_ISSUES_FILE"])
    return list(_issues(str(path), _stamp(path)))
