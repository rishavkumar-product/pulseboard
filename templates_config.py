"""
Shared Jinja2Templates instance with custom filters.
All routers should import `templates` from here instead of creating their own.
"""
from fastapi.templating import Jinja2Templates
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))


def to_ist(value: str, fmt: str = "%d %b %Y, %I:%M %p") -> str:
    """
    Convert a UTC timestamp string (from SQLite) to IST and format it.
    Accepts strings like "2026-05-10 08:30:00" or "2026-05-10T08:30:00".
    Returns formatted string, e.g. "10 May 2026, 02:00 PM".
    Falls back to the raw value on any parse error.
    """
    if not value:
        return ""
    try:
        # Handle both space-separated and ISO 8601 separators
        clean = value.replace("T", " ").split(".")[0].strip()
        # Parse as UTC
        dt_utc = datetime.strptime(clean, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        dt_ist = dt_utc.astimezone(IST)
        return dt_ist.strftime(fmt)
    except Exception:
        return str(value)


def to_ist_date(value: str) -> str:
    """Short date-only variant: '10 May 2026'."""
    return to_ist(value, fmt="%d %b %Y")


templates = Jinja2Templates(directory="templates")
templates.env.filters["to_ist"] = to_ist
templates.env.filters["to_ist_date"] = to_ist_date
