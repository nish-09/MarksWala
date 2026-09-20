from __future__ import annotations

import logging
import sys

from app.core.config import settings


def configure_logging(force: bool = False) -> None:
    """Idempotent. `force` re-applies our handler (Dramatiq reconfigures logging inside worker processes)."""
    root = logging.getLogger()
    if getattr(root, "_markswala_configured", False) and not force:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level.upper())
    for noisy in ("httpx", "httpcore", "urllib3", "google_genai", "qdrant_client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._markswala_configured = True  # type: ignore[attr-defined]
