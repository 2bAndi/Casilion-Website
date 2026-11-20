"""Configuration of the AI worker, from environment variables (and .env in the repository root)."""
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Config:
    job_api: str
    token: str
    worker_id: str
    poll_seconds: float
    backend: str  # "stub" (rules, no model) | "openai" (any OpenAI-compatible endpoint, e.g. Ollama)
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    llm_timeout: float
    public_dir: Path
    known_issues_file: Path
    repo_dir: Path | None
    canaries: list[str] = field(default_factory=list)


def load() -> Config:
    _load_dotenv(ROOT / ".env")
    env = os.environ.get
    repo = env("PRODUCT_REPO_DIR")
    canaries = [c.strip() for c in (env("CANARY_STRINGS") or "").split(",") if c.strip()]
    canary_file = ROOT / "knowledge" / "internal" / "canaries.txt"
    if canary_file.exists():
        canaries += [line.strip() for line in canary_file.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
    return Config(
        job_api=(env("CASSILION_JOB_API") or "http://127.0.0.1:8000").rstrip("/"),
        token=env("AI_WORKER_TOKEN") or "dev-worker-token",
        worker_id=env("AI_WORKER_ID") or socket.gethostname(),
        poll_seconds=float(env("AI_POLL_SECONDS") or 3),
        backend=(env("LLM_BACKEND") or "stub").lower(),
        llm_base_url=(env("LLM_BASE_URL") or "http://127.0.0.1:11434/v1").rstrip("/"),
        llm_model=env("LLM_MODEL") or "",
        llm_api_key=env("LLM_API_KEY") or "ollama",
        llm_timeout=float(env("LLM_TIMEOUT") or 300),
        public_dir=Path(env("KNOWLEDGE_PUBLIC_DIR") or ROOT / "knowledge" / "public"),
        known_issues_file=Path(env("KNOWN_ISSUES_FILE") or ROOT / "knowledge" / "known_issues.json"),
        repo_dir=Path(repo) if repo else None,
        canaries=canaries,
    )
