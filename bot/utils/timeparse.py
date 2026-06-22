from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_RE_DURATION = re.compile(r"^\s*(\d+)\s*([smhdwSMHDW])\s*$")
_UNITS = {
    "s": "seconds",
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}

_ABSOLUTE_FORMATS = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S",
)


def parse_end_at(text: str, tz_name: str = "UTC") -> datetime | None:
    text = (text or "").strip()
    if not text:
        return None

    m = _RE_DURATION.match(text)
    if m:
        n = int(m.group(1))
        if n <= 0:
            return None
        unit = _UNITS[m.group(2).lower()]
        return datetime.now(timezone.utc) + timedelta(**{unit: n})

    for fmt in _ABSOLUTE_FORMATS:
        try:
            local = datetime.strptime(text, fmt)
        except ValueError:
            continue
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = timezone.utc
        return local.replace(tzinfo=tz).astimezone(timezone.utc)

    return None


def format_local(dt: datetime, tz_name: str = "UTC") -> str:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M")


def parse_prizes(text: str) -> list[dict] | None:
    out: list[dict] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        sep = ":" if ":" in line else ("：" if "：" in line else None)
        if sep is None:
            return None
        name, _, count_str = line.partition(sep)
        name = name.strip()
        count_str = count_str.strip()
        if not name or not count_str.isdigit():
            return None
        count = int(count_str)
        if count <= 0:
            return None
        out.append({"name": name, "count": count})
    return out or None


def parse_channels(text: str) -> list[str]:
    text = (text or "").strip()
    if not text or text == "-":
        return []
    parts: list[str] = []
    for piece in text.replace(",", " ").replace("\n", " ").split():
        piece = piece.strip()
        if not piece:
            continue
        if not piece.startswith("@"):
            piece = "@" + piece
        parts.append(piece)
    return parts
