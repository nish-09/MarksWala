"""Dramatiq actors. A single actor runs any job kind; all state lives in `processing_jobs`.

Start a worker:  dramatiq app.tasks.actors --processes 1 --threads 4
"""
import threading
import time
import uuid
import logging

import dramatiq
from dramatiq.middleware import Middleware

from app.tasks.broker import broker
from app.services import jobs

log = logging.getLogger("MarksWala.worker")


class Reaper(Middleware):
    """Runs inside the worker process: periodically recovers crashed/lost jobs."""

    def after_worker_boot(self, broker, worker):
        from app.core.logging import configure_logging

        configure_logging(force=True)
        log.info("worker booted; job reaper started")

        def loop():
            while True:
                try:
                    jobs.reap_stale_jobs()
                except Exception:  # keep the reaper alive
                    log.exception("reaper iteration failed")
                time.sleep(60)

        threading.Thread(target=loop, name="job-reaper", daemon=True).start()


broker.add_middleware(Reaper())


@dramatiq.actor(actor_name="run_job", max_retries=0, time_limit=30 * 60 * 1000)
def run_job_actor(job_id: str) -> None:
    jobs.run_job(uuid.UUID(job_id))
