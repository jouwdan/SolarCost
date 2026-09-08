"""Local daily accounting. No Home Assistant or third-party dependencies."""

import json
import math
import re
import sqlite3
from bisect import bisect_right
from calendar import monthrange
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .const import ENERGY

UTC = timezone.utc
COLUMNS = ENERGY + ("import_cost", "export_credit", "standing_charge", "fixed_charge")


def finite_number(value, *, minimum=None):
    """Validate measurements and tariffs before they reach the ledger."""
    if isinstance(value, bool):
        raise ValueError("A number is required")
    number = float(value)
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        raise ValueError("Number outside the allowed range")
    return number


def minute(value):
    """Accept minute-resolution wall-clock times from HA's time selector."""
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}(:00)?", value):
        raise ValueError("Use HH:MM, with zero seconds")
    hour, minutes = map(int, value[:5].split(":"))
    if hour > 23 or minutes > 59:
        raise ValueError("Invalid time")
    return hour * 60 + minutes


def validate_tariff(config):
    """Reject ambiguous schedules, including overlaps across Sunday midnight."""
    for key in ("import_rate", "export_rate", "standing_charge"):
        finite_number(config[key], minimum=0 if key == "standing_charge" else None)
    finite_number(config.get("monthly_charge", 0), minimum=0)
    for key in ("vat_percent", "discount_percent"):
        if finite_number(config.get(key, 0), minimum=0) > 100:
            raise ValueError("Percentages must be between 0 and 100")
    if len(config["bands"]) > 48:
        raise ValueError("At most 48 time bands")
    spans, names = [], set()
    for band in config["bands"]:
        name = band["name"].strip()
        if not name or len(name) > 80 or name in names:
            raise ValueError("Band names must be unique and between 1 and 80 characters")
        names.add(name)
        start, end = minute(band["start"]), minute(band["end"])
        if start == end:
            raise ValueError("Start and end must differ; use base rates for a full day")
        days = band["days"]
        if (
            not days
            or len(set(days)) != len(days)
            or any(str(d) not in "0123456" or len(str(d)) != 1 for d in days)
        ):
            raise ValueError("Choose weekdays")
        for key in ("import_rate", "export_rate"):
            finite_number(band[key])
        for day in days:
            left = int(day) * 1440 + start
            right = int(day) * 1440 + end + (1440 if end < start else 0)
            spans.append((left, min(right, 10080)))
            if right > 10080:
                spans.append((0, right - 10080))
    spans.sort()
    if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
        raise ValueError("Time bands overlap")


def rates_at(config, local):
    """An overnight band's selected weekday is the day it starts."""
    at = local.hour * 60 + local.minute
    multiplier = (1 - float(config.get("discount_percent", 0)) / 100) * (
        1 + float(config.get("vat_percent", 0)) / 100
    )
    for band in config["bands"]:
        start, end = minute(band["start"]), minute(band["end"])
        weekday = local.weekday()
        if end < start and at < end:
            weekday = (weekday - 1) % 7
        active = start <= at < end if start < end else at >= start or at < end
        if active and str(weekday) in {str(day) for day in band["days"]}:
            return float(band["import_rate"]) * multiplier, float(band["export_rate"])
    return float(config["import_rate"]) * multiplier, float(config["export_rate"])


def midnight(day, zone):
    return datetime.combine(day, time(), zone).timestamp()


def segments(start, end, zone, versions):
    """Split elapsed UTC time at days, tariff changes and DST transitions."""
    effective = [item[0] for item in versions]
    day = datetime.fromtimestamp(start, zone).date()
    while midnight(day, zone) < end:
        next_day = day + timedelta(days=1)
        day_start, day_end = midnight(day, zone), midnight(next_day, zone)
        left, right = max(start, day_start), min(end, day_end)
        if right <= left:
            day = next_day
            continue
        cuts = {left, right}
        first = max(0, bisect_right(effective, left) - 1)
        last = bisect_right(effective, right)
        for changed, config in versions[first:last]:
            if left < changed < right:
                cuts.add(changed)
            for band in config["bands"]:
                for edge in (band["start"], band["end"]):
                    minutes = minute(edge)
                    wall = datetime.combine(day, time(minutes // 60, minutes % 60), zone)
                    for fold in (0, 1):
                        point = wall.replace(fold=fold).timestamp()
                        if left < point < right:
                            cuts.add(point)
        # Find offset jumps as well: a band's start can be inside a skipped hour.
        point = day_start
        while point < day_end:
            following = min(point + 3600, day_end)
            offset = datetime.fromtimestamp(point, zone).utcoffset()
            if offset != datetime.fromtimestamp(following, zone).utcoffset():
                low, high = int(point), int(following)
                while high - low > 1:
                    middle = (low + high) // 2
                    if datetime.fromtimestamp(middle, zone).utcoffset() == offset:
                        low = middle
                    else:
                        high = middle
                if left < high < right:
                    cuts.add(float(high))
            point = following
        points = sorted(cuts)
        for begin, finish in zip(points, points[1:]):
            middle = (begin + finish) / 2
            config = versions[max(0, bisect_right(effective, middle) - 1)][1]
            import_rate, export_rate = rates_at(config, datetime.fromtimestamp(middle, zone))
            tax = 1 + float(config.get("vat_percent", 0)) / 100
            yield (
                day.isoformat(),
                finish - begin,
                day_end - day_start,
                import_rate,
                export_rate,
                float(config["standing_charge"]) * tax,
                float(config.get("monthly_charge", 0)) * tax / monthrange(day.year, day.month)[1],
            )
        day = next_day


def period_starts(today, months, years):
    """Last N periods includes this partial calendar month/year."""
    month_index = today.year * 12 + today.month - months
    return {
        "today": today,
        "week": today - timedelta(days=today.weekday()),
        "month": today.replace(day=1),
        "last_month": (today.replace(day=1) - timedelta(days=1)).replace(day=1),
        "last_months": date(month_index // 12, month_index % 12 + 1, 1),
        "year": today.replace(month=1, day=1),
        "last_years": date(today.year - years + 1, 1, 1),
        "all_time": date(1, 1, 1),
    }


def totals(row):
    values = {key: float(row[key] or 0) for key in COLUMNS}
    values["net_cost"] = (
        values["import_cost"]
        + values["standing_charge"]
        + values["fixed_charge"]
        - values["export_credit"]
    )
    return values


class Ledger:
    """One indexed row per day, plus four meter checkpoints and tariff history."""

    def __init__(self, path, time_zone, currency):
        self.path = Path(path)
        self.zone = ZoneInfo(time_zone)
        self.currency = currency

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                db.execute("BEGIN")
                yield db
        finally:
            db.close()

    def initialize(self, now):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(
                    "Unsupported SolarCost database version; restore a compatible integration"
                )
            db.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS tariffs (effective REAL PRIMARY KEY, config TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS readings (source TEXT PRIMARY KEY, entity TEXT NOT NULL, value REAL NOT NULL, at REAL NOT NULL, resets INTEGER NOT NULL DEFAULT 0, corrections INTEGER NOT NULL DEFAULT 0)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS daily (day TEXT PRIMARY KEY, "
                + ", ".join(f"{key} REAL NOT NULL DEFAULT 0" for key in COLUMNS)
                + ")"
            )
            for key, value in {
                "since": str(now),
                "standing_until": str(now),
                "time_zone": self.zone.key,
                "currency": self.currency,
            }.items():
                db.execute("INSERT OR IGNORE INTO meta VALUES (?, ?)", (key, value))
            meta = dict(db.execute("SELECT key, value FROM meta"))
            if meta["time_zone"] != self.zone.key or meta["currency"] != self.currency:
                raise ValueError("Existing ledger has a different currency or time zone")
            db.execute("PRAGMA user_version=1")

    @staticmethod
    def add(db, day, values):
        # Columns are internal constants, never user-provided SQL identifiers.
        columns = list(values)
        db.execute(
            "INSERT INTO daily (day, "
            + ", ".join(columns)
            + ") VALUES (?"
            + ", ?" * len(columns)
            + ") ON CONFLICT(day) DO UPDATE SET "
            + ", ".join(f"{key} = {key} + excluded.{key}" for key in columns),
            (day, *values.values()),
        )

    def update(self, config, samples, now):
        """Commit deltas and checkpoints together, making restarts idempotent."""
        tariff = {
            key: config[key] for key in ("import_rate", "export_rate", "standing_charge", "bands")
        }
        tariff.update(
            {
                key: config.get(key, 0)
                for key in ("monthly_charge", "vat_percent", "discount_percent")
            }
        )
        validate_tariff(tariff)
        encoded = json.dumps(tariff, sort_keys=True)
        with self.connection() as db:
            previous = db.execute(
                "SELECT config FROM tariffs ORDER BY effective DESC LIMIT 1"
            ).fetchone()
            since = float(db.execute("SELECT value FROM meta WHERE key='since'").fetchone()[0])
            if previous is None or previous[0] != encoded:
                db.execute(
                    "INSERT INTO tariffs VALUES (?, ?)",
                    (since if previous is None else now, encoded),
                )
            versions = [
                (row[0], json.loads(row[1]))
                for row in db.execute("SELECT * FROM tariffs ORDER BY effective")
            ]
            standing_until = float(
                db.execute("SELECT value FROM meta WHERE key='standing_until'").fetchone()[0]
            )
            if now > standing_until:
                for day, seconds, day_seconds, _, _, standing, fixed in segments(
                    standing_until, now, self.zone, versions
                ):
                    self.add(
                        db,
                        day,
                        {
                            "standing_charge": standing * seconds / day_seconds,
                            "fixed_charge": fixed * seconds / day_seconds,
                        },
                    )
                db.execute("UPDATE meta SET value=? WHERE key='standing_until'", (str(now),))
            for source, sample in samples:
                value = finite_number(sample["value"], minimum=0)
                at = finite_number(sample["at"])
                if at > now:
                    continue
                previous = db.execute("SELECT * FROM readings WHERE source=?", (source,)).fetchone()
                if previous is None or previous["entity"] != sample["entity"]:
                    db.execute(
                        "INSERT OR REPLACE INTO readings (source, entity, value, at) VALUES (?, ?, ?, ?)",
                        (source, sample["entity"], value, max(at, now if previous else since)),
                    )
                    continue
                if at <= previous["at"]:
                    continue
                delta = value - previous["value"]
                start = previous["at"]
                reset_at = sample.get("last_reset")
                reset = (sample["resetting"] and delta < 0) or (
                    reset_at is not None and start < reset_at <= at
                )
                correction = delta < 0 and not reset
                if reset:
                    delta = value
                    if reset_at is not None and start < reset_at < at:
                        start = reset_at
                if not correction:
                    # ponytail: uniform use between readings; use interval meters for exact TOU allocation.
                    for day, seconds, _, import_rate, export_rate, _, _ in segments(
                        start, at, self.zone, versions
                    ):
                        energy = delta * seconds / (at - start)
                        values = {f"{source}_kwh": energy}
                        if source == "import":
                            values["import_cost"] = energy * import_rate
                        elif source == "export":
                            values["export_credit"] = energy * export_rate
                        self.add(db, day, values)
                db.execute(
                    "UPDATE readings SET value=?, at=?, resets=resets+?, corrections=corrections+? WHERE source=?",
                    (value, at, int(reset), int(correction), source),
                )

    @staticmethod
    def aggregate(db, start, end):
        row = db.execute(
            "SELECT "
            + ", ".join(f"SUM({key}) AS {key}" for key in COLUMNS)
            + " FROM daily WHERE day >= ? AND day <= ?",
            (start.isoformat(), end.isoformat()),
        ).fetchone()
        return totals(row)

    def snapshot(self, now, months, years):
        today = datetime.fromtimestamp(now, self.zone).date()
        with self.connection() as db:
            meta = dict(db.execute("SELECT key, value FROM meta"))
            since = datetime.fromtimestamp(float(meta["since"]), self.zone)
            periods = {}
            for period, start in period_starts(today, months, years).items():
                end = today.replace(day=1) - timedelta(days=1) if period == "last_month" else today
                has_history = since.date() <= end
                periods[period] = {
                    **self.aggregate(db, start, end),
                    "start": (max(start, since.date()) if has_history else start).isoformat(),
                    "end": end.isoformat(),
                    "has_history": has_history,
                    "partial_history": period != "all_time"
                    and midnight(start, self.zone) < float(meta["since"]),
                }
            config = json.loads(
                db.execute("SELECT config FROM tariffs ORDER BY effective DESC LIMIT 1").fetchone()[
                    0
                ]
            )
            return {
                "periods": periods,
                "since": since.isoformat(),
                "updated_at": datetime.fromtimestamp(now, UTC).isoformat(),
                "rates": rates_at(config, datetime.fromtimestamp(now, self.zone)),
                "readings": {
                    row["source"]: dict(row) for row in db.execute("SELECT * FROM readings")
                },
            }

    def report(self, start, end, group_by):
        """Query complete local dates; response size grows with days, not samples."""
        if end < start or group_by not in ("day", "week", "month", "year"):
            raise ValueError("Choose an ordered date range and a valid grouping")
        with self.connection() as db:
            groups = {}
            for row in db.execute(
                "SELECT * FROM daily WHERE day >= ? AND day <= ? ORDER BY day",
                (start.isoformat(), end.isoformat()),
            ):
                day = date.fromisoformat(row["day"])
                key = {
                    "day": day.isoformat(),
                    "week": (day - timedelta(days=day.weekday())).isoformat(),
                    "month": day.strftime("%Y-%m"),
                    "year": str(day.year),
                }[group_by]
                group = groups.setdefault(key, dict.fromkeys(COLUMNS, 0.0))
                for column in COLUMNS:
                    group[column] += row[column]
            meta = dict(db.execute("SELECT key, value FROM meta"))
            since = float(meta["since"])
            return {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "currency": self.currency,
                "time_zone": self.zone.key,
                "tracking_since": datetime.fromtimestamp(since, self.zone).isoformat(),
                "partial_history": midnight(start, self.zone) < since,
                "totals": {
                    key: round(value, 6) for key, value in self.aggregate(db, start, end).items()
                },
                "periods": [
                    {
                        "period": key,
                        **{metric: round(value, 6) for metric, value in totals(group).items()},
                    }
                    for key, group in groups.items()
                ],
            }
