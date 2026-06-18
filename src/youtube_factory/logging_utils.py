"""
Centralized logging for YouTube Factory pipeline.

Replaces all per-agent _safe_print hacks, __builtins__ print overrides,
and _init_logging/_log/_close_logging patterns with Python's standard
logging module.

Usage:
    from youtube_factory.logging_utils import get_logger

    log = get_logger("agent_idea")
    log.info("Pipeline stage starting...")
    log.warning("Something suspicious: %s", value)
    log.error("Something failed: %s", exc_info=True)
"""

import logging
import os
import sys

# ---------------------------------------------------------------------------
# Custom StreamHandler that won't crash on Windows cp1252 encoding errors
# ---------------------------------------------------------------------------

class _SafeStreamHandler(logging.StreamHandler):
    """A StreamHandler that silently swallows encoding errors (common on
    Windows consoles with cp1252 when the LLM returns fancy Unicode)."""

    def emit(self, record):
        try:
            super().emit(record)
        except Exception:
            # If even the fallback fails, give up silently rather than crash
            try:
                # Last-resort: write ASCII-only version
                msg = self.format(record)
                safe = msg.encode("ascii", errors="replace").decode("ascii")
                print(safe, file=sys.stderr)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Global registry of run-specific file handlers (keyed by logger name)
# so we can close them when a run ends.
# ---------------------------------------------------------------------------

_run_file_handlers: dict[str, logging.FileHandler] = {}


# ---------------------------------------------------------------------------
# Logger factory
# ---------------------------------------------------------------------------

def get_logger(name: str) -> logging.Logger:
    """Return a logger for a pipeline component.

    The returned logger has a single console handler that is safe for
    Windows cp1252 terminals.  File handlers are added separately via
    :func:`add_run_file_handler` when a pipeline run starts.
    """
    logger = logging.getLogger(f"pipeline.{name}")

    # Only attach our console handler once
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        console = _SafeStreamHandler(sys.stdout)
        console.setLevel(logging.INFO)
        console.setFormatter(logging.Formatter(
            "[%(name)s] %(levelname)s: %(message)s",
        ))
        logger.addHandler(console)

    return logger


# ---------------------------------------------------------------------------
# Run-level file logging
# ---------------------------------------------------------------------------

def add_run_file_handler(name: str, run_dir: str) -> logging.Logger:
    """Add a file handler to the logger *name* that writes to
    ``run_dir / {name}.log``.

    Returns the logger for convenience.
    """
    logger = get_logger(name)
    log_path = os.path.join(run_dir, f"{name}.log")

    handler = logging.FileHandler(log_path, encoding="utf-8", mode="a")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    ))
    logger.addHandler(handler)

    _run_file_handlers[f"{name}.{run_dir}"] = handler
    return logger


def remove_run_file_handler(name: str, run_dir: str) -> None:
    """Detach and close the file handler attached to *name* for *run_dir*."""
    key = f"{name}.{run_dir}"
    handler = _run_file_handlers.pop(key, None)
    if handler is not None:
        logger = logging.getLogger(f"pipeline.{name}")
        logger.removeHandler(handler)
        handler.close()


def close_all_run_handlers() -> None:
    """Close every open run file handler (called during shutdown)."""
    for key, handler in list(_run_file_handlers.items()):
        try:
            handler.close()
        except Exception:
            pass
    _run_file_handlers.clear()
