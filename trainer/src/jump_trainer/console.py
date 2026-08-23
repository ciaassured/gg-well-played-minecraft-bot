"""Consistent, flushed console records for long-running trainer commands."""

from __future__ import annotations

import sys


def format_duration(seconds: float) -> str:
    total_seconds = max(0, round(seconds))
    hours, remainder = divmod(total_seconds, 3_600)
    minutes, remaining_seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{remaining_seconds:02d}"
    return f"{minutes:02d}:{remaining_seconds:02d}"


def emit(channel: str, subject: str, detail: str) -> None:
    """Write one machine-greppable, human-readable status record."""

    print(f"[{channel}] {subject}: {detail}", file=sys.stderr, flush=True)
