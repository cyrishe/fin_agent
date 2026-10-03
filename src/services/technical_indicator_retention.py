"""Select date-level archive candidates, without reading or deleting any data."""
from collections.abc import Iterable
from datetime import date


DEFAULT_TECHNICAL_RESULT_KEEP_DATES = 252


def technical_result_archive_dates(
    published_trade_dates: Iterable[date], *,
    referenced_trade_dates: Iterable[date] = (),
    keep_dates: int = DEFAULT_TECHNICAL_RESULT_KEEP_DATES,
) -> tuple[date, ...]:
    """Return old result dates outside the latest N distinct published dates.

    Publication dates, not wall-clock days, determine retention. Any report or
    pending run reference pins its whole date conservatively, including every
    revision. Callers must supply the complete reference set for this universe
    and price basis. Empty references are appropriate only when none exist.
    These candidates still require archive/readback/reference verification; they
    are NOT permission to delete input facts, evidence, checkpoints or DB rows.
    """
    if isinstance(keep_dates, bool) or not isinstance(keep_dates, int) or keep_dates < 1:
        raise ValueError("keep_dates must be a positive integer")
    dates = set(published_trade_dates)
    pinned = set(referenced_trade_dates)
    if any(type(value) is not date for value in dates | pinned):
        raise ValueError("trade dates must be date objects, without timestamps")
    older = sorted(dates)[:-keep_dates]
    return tuple(value for value in older if value not in pinned)
