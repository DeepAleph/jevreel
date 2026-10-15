"""Timestamps as people and agents write them."""


def clock(seconds: float) -> str:
    """0:07, 12:04, 1:02:03."""
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def span(start: float, end: float) -> str:
    return f"{clock(start)}-{clock(end)}"
