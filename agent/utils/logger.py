"""
Timestamped logger for the Lead Generation Agent.
Each run gets its own log file; all output also goes to stdout for Railway.
"""
from __future__ import annotations

import logging

from agent.utils.client_paths import client_runs_dir


def get_logger(
    name: str,
    client_id: str | None = None,
    run_id: str | None = None,
    *,
    keyword: str | None = None,
) -> logging.Logger:
    """
    Return a logger that writes to:
    - stdout (always) — picked up by Railway logs
    - data/clients/{client_id}/[smoke/]runs/{run_id}.log (if both provided)
    """
    logger = logging.getLogger(name)

    # Avoid duplicate handlers if called multiple times
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    logger.addHandler(console)

    if client_id and run_id:
        log_dir = client_runs_dir(client_id, keyword=keyword)
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_dir / f"{run_id}.log", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger


def get_run_logger(run_id: str, client_id: str, *, keyword: str | None = None) -> logging.Logger:
    """Per-run logger; smoke keywords land under clients/{id}/smoke/runs/."""
    return get_logger(f"run.{run_id}", client_id=client_id, run_id=run_id, keyword=keyword)


def attach_leadgen_mirror(run_logger: logging.Logger) -> list[logging.Handler]:
    """Copy this run's file handler onto the process logger.

    treg and Apify log on ``leadgen``, which otherwise only hits the console.
    A posts or jobs failure then never appears in the run log, and the list
    still completes as a success.
    """
    leadgen = logging.getLogger("leadgen")
    added: list[logging.Handler] = []
    for handler in run_logger.handlers:
        if isinstance(handler, logging.FileHandler) and handler not in leadgen.handlers:
            leadgen.addHandler(handler)
            added.append(handler)
    return added


def detach_leadgen_mirror(handlers: list[logging.Handler]) -> None:
    leadgen = logging.getLogger("leadgen")
    for handler in handlers:
        leadgen.removeHandler(handler)


# Module-level logger for startup/health/auth messages
log = get_logger("leadgen")
