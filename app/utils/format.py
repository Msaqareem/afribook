"""Template display helpers."""


def kes(value) -> str:
    """1500 -> 'KES 1,500' (whole shillings)."""
    return f"KES {int(value):,}"


def hhmm(value) -> str:
    """time or datetime -> '8:00 AM' (12-hour clock, as Kenyans read it)."""
    if getattr(value, "tzinfo", None) is not None:  # aware datetimes are shown in Kenya time
        from app.utils.timeutil import to_nairobi

        value = to_nairobi(value)
    return value.strftime("%I:%M %p").lstrip("0")


def local_dt(value, fmt: str = "%a %d %b %Y, %I:%M %p") -> str:
    """Timezone-aware datetime -> Africa/Nairobi wall-clock text."""
    from app.utils.timeutil import to_nairobi

    return to_nairobi(value).strftime(fmt).replace(", 0", ", ")


def duration(minutes) -> str:
    """45 -> '45 min', 60 -> '1 h', 90 -> '1 h 30 min'."""
    hours, mins = divmod(int(minutes), 60)
    parts = []
    if hours:
        parts.append(f"{hours} h")
    if mins:
        parts.append(f"{mins} min")
    return " ".join(parts) or "0 min"
