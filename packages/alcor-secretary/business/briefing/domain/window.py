"""UTC instants for collection; only the calendar-day boundary uses local time."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def instant(value):
    if not isinstance(value, str):
        raise ValueError("缺少有效的简报时间。")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("简报时间必须包含时区。")
    return stamp.astimezone(timezone.utc)


def utc(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def previous_midnight(day, zone=None):
    midnight = datetime.combine(date.fromisoformat(day) - timedelta(days=1), time.min)
    return midnight.replace(tzinfo=ZoneInfo(zone)) if zone else midnight.astimezone()


def collection_window(end, checkpoint, zone=None):
    finish = instant(end)
    local = finish.astimezone(ZoneInfo(zone)) if zone else finish.astimezone()
    floor = previous_midnight(local.date().isoformat(), zone)
    after = instant(checkpoint) if checkpoint else None
    if after and after > finish:
        raise ValueError("生成时间早于已提交的简报检查点。")
    start = max(after, floor) if after else floor
    return dict(
        day=local.date().isoformat(),
        zone=zone or "system",
        floor=utc(floor),
        start=utc(start),
        end=utc(finish),
        previous=checkpoint,
        clipped=bool(after and after < floor),
        include_start=after is None or after < floor,
    )


def in_window(value, window):
    when = instant(value)
    start = instant(window["start"])
    return (when > start or (window["include_start"] and when == start)) and when <= instant(window["end"])
