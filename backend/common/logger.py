"""
logger.py — Structured, colour-coded console logger for backend processes.
"""

import logging
import sys
from datetime import datetime


_RESET = "\033[0m"
_BOLD = "\033[1m"
_CYAN = "\033[96m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_MAGENTA = "\033[95m"


class _ColourFormatter(logging.Formatter):
    COLOURS = {
        logging.DEBUG: _CYAN,
        logging.INFO: _GREEN,
        logging.WARNING: _YELLOW,
        logging.ERROR: _RED,
        logging.CRITICAL: _MAGENTA,
    }

    def format(self, record: logging.LogRecord) -> str:
        colour = self.COLOURS.get(record.levelno, _RESET)
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        tag = record.name.upper()
        level = record.levelname[0]  # D/I/W/E/C
        msg = record.getMessage()
        return f"{_BOLD}{ts}{_RESET} {colour}[{tag}]{_RESET} {level} {msg}"


def get_logger(name: str, level: int = logging.DEBUG) -> logging.Logger:
    """Return a named logger with colour output to stderr."""
    log = logging.getLogger(name)
    if not log.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(_ColourFormatter())
        log.addHandler(handler)
    log.setLevel(level)
    log.propagate = False
    return log
