"""Time helpers. Store UTC, display Africa/Nairobi."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

NAIROBI = ZoneInfo("Africa/Nairobi")


def utcnow() -> datetime:
    """Timezone-aware current UTC time."""
    return datetime.now(timezone.utc)


def to_nairobi(value: datetime) -> datetime:
    return value.astimezone(NAIROBI)
