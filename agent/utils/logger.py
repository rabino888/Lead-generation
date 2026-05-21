"""
Timestamped logger for the Lead Generation Agent.
Each run gets its own log file; all output also goes to stdout for Railway.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path


def get_logger(name: str, client_id: str | None = None, run_id: str | None = None) -> logging.Logger:
    """
    Return a logger that writes to:
    - stdout (always) — picked up by Railway logs
    - data/clients/{client_id}/runs/{run_id}.log (if both provided)
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

    # Console handler (Railway stdout)
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    logger.addHandler(console)

    # File handler (per-run log)
    if client_id and run_id:
        log_dir = Path("data") / "clients" / client_id / "runs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{run_id}.log"

        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)

    return logger


def get_run_logger(run_id: str, client_id: str) -> logging.Logger:
    """Convenience: get a logger scoped to a specific run."""
    return get_logger(f"run.{run_id}", client_id=client_id, run_id=run_id)


# Module-level logger for startup/health/auth messages
log = get_logger("leadgen")
