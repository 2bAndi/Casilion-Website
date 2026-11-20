"""The AI worker: claims support jobs, analyses them, posts the result.

    python -m ai_worker            run until stopped
    python -m ai_worker --once     handle at most one job, then exit

It only ever opens connections to the job API (and to the model endpoint). It
needs no inbound port.
"""
import argparse
import logging
import sys
import time

from .client import JobApi, JobApiError
from .config import load
from .llm import LLMError
from .pipeline import Pipeline

log = logging.getLogger("ai_worker")


def main() -> int:
    parser = argparse.ArgumentParser(prog="ai_worker")
    parser.add_argument("--once", action="store_true", help="handle at most one job, then exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s ai_worker: %(message)s")

    config = load()
    try:
        pipeline = Pipeline(config)
    except LLMError as exc:
        log.error("cannot start: %s (LLM_BACKEND=%s)", exc, config.backend)
        return 2
    api = JobApi(config.job_api, config.token, config.worker_id)
    log.info("polling %s as %s", config.job_api, config.worker_id)

    delay = config.poll_seconds
    while True:
        try:
            job = api.claim()
        except JobApiError as exc:
            if args.once:
                log.error("%s", exc)
                return 1
            delay = min(delay * 2, 60)
            log.warning("%s; next try in %.0fs", exc, delay)
            time.sleep(delay)
            continue
        delay = config.poll_seconds

        if job is None:
            if args.once:
                return 0
            time.sleep(delay)
            continue

        job_id, ticket_id = job["job_id"], job["ticket"]["id"]
        try:
            result = pipeline.run(job)
            answer = api.result(job_id, result)
            log.info(
                "%s: %s (%s) -> %s",
                ticket_id,
                result["finding"]["classification"],
                result["finding"]["confidence"],
                answer.get("ticket_status", "?"),
            )
        except JobApiError as exc:
            log.error("%s: could not post the result: %s", ticket_id, exc)
        except Exception as exc:  # the job must not be lost silently; say what kind of failure, not its content
            log.exception("%s: analysis failed", ticket_id)
            try:
                api.fail(job_id, f"worker error: {type(exc).__name__}")
            except JobApiError:
                pass
        if args.once:
            return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
