"""Timestamp helpers. All stored timestamps are naive UTC datetimes."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any


def utcnow() -> datetime:
    """Naive UTC 'now' (what we persist)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def parse_timestamp(value: Any) -> datetime:
    """Parse ISO-8601 strings or epoch numbers (s / ms / us / ns) into naive UTC.

    Raises ValueError on anything that cannot be interpreted.
    """
    if isinstance(value, datetime):
        return to_naive_utc(value)
    if isinstance(value, bool):
        raise ValueError("timestamp must not be a boolean")
    if isinstance(value, (int, float)):
        return _from_epoch(float(value))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("timestamp is empty")
        # numeric strings (epoch)
        try:
            return _from_epoch(float(text))
        except ValueError:
            pass
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            return to_naive_utc(datetime.fromisoformat(text))
        except ValueError as exc:
            raise ValueError(f"invalid ISO-8601 timestamp: {value!r}") from exc
    raise ValueError(f"unsupported timestamp type: {type(value).__name__}")


def _from_epoch(number: float) -> datetime:
    if not math.isfinite(number) or number < 0:
        raise ValueError("epoch timestamp out of range")
    magnitude = abs(number)
    if magnitude >= 1e17:  # nanoseconds
        seconds = number / 1e9
    elif magnitude >= 1e14:  # microseconds
        seconds = number / 1e6
    elif magnitude >= 1e11:  # milliseconds
        seconds = number / 1e3
    else:
        seconds = number
    try:
        return datetime(1970, 1, 1) + timedelta(seconds=seconds)
    except OverflowError as exc:
        raise ValueError("epoch timestamp out of range") from exc


def to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return to_naive_utc(dt).isoformat(timespec="microseconds") + "Z"


def duration_ms(start: datetime, end: datetime) -> float:
    return round((end - start).total_seconds() * 1000.0, 3)


def floor_to_bucket(dt: datetime, bucket_seconds: int) -> datetime:
    epoch = datetime(1970, 1, 1)
    seconds = int((dt - epoch).total_seconds())
    return epoch + timedelta(seconds=(seconds // bucket_seconds) * bucket_seconds)
