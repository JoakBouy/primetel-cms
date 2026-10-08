"""
Reporting periods.

A Period is a half-open range of local calendar dates [start, end] with a
bucket size for trend charts. Periods are anchored on any date inside them,
so "?period=quarter&date=2026-05-14" means Q2 2026, and prev/next step by a
whole period.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

PERIOD_CHOICES = [
    ("day", _("Day")),
    ("week", _("Week")),
    ("month", _("Month")),
    ("quarter", _("Quarter")),
    ("year", _("Year")),
    ("custom", _("Custom")),
]
PERIOD_KEYS = {key for key, _label in PERIOD_CHOICES}

MAX_CUSTOM_DAYS = 3 * 366


@dataclass(frozen=True)
class Period:
    kind: str
    start: date  # first day, inclusive
    end: date    # last day, inclusive

    # ── Bounds as aware datetimes in the clinic timezone ──
    @property
    def start_dt(self) -> datetime:
        return timezone.make_aware(datetime.combine(self.start, time.min))

    @property
    def end_dt(self) -> datetime:
        """Exclusive upper bound (midnight after the last day)."""
        return timezone.make_aware(datetime.combine(self.end + timedelta(days=1), time.min))

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def label(self) -> str:
        if self.kind == "day":
            return self.start.strftime("%d %b %Y")
        if self.kind == "week":
            iso = self.start.isocalendar()
            return f"{_('Week')} {iso.week}, {iso.year} ({self.start:%d %b} – {self.end:%d %b})"
        if self.kind == "month":
            return self.start.strftime("%B %Y")
        if self.kind == "quarter":
            return f"Q{(self.start.month - 1) // 3 + 1} {self.start.year}"
        if self.kind == "year":
            return str(self.start.year)
        return f"{self.start:%d %b %Y} – {self.end:%d %b %Y}"

    # ── Navigation ──
    def shifted(self, steps: int) -> "Period":
        """The period `steps` whole periods later (negative = earlier)."""
        if self.kind == "custom":
            delta = timedelta(days=self.days * steps)
            return Period("custom", self.start + delta, self.end + delta)
        if self.kind == "day":
            return period_for("day", self.start + timedelta(days=steps))
        if self.kind == "week":
            return period_for("week", self.start + timedelta(weeks=steps))
        months = {"month": 1, "quarter": 3, "year": 12}[self.kind] * steps
        y, m = divmod(self.start.month - 1 + months, 12)
        return period_for(self.kind, date(self.start.year + y, m + 1, 1))

    @property
    def previous(self) -> "Period":
        return self.shifted(-1)

    @property
    def next(self) -> "Period":
        return self.shifted(1)

    @property
    def is_current_or_future(self) -> bool:
        return self.end >= timezone.localdate()

    def query(self) -> str:
        if self.kind == "custom":
            return f"period=custom&start={self.start.isoformat()}&end={self.end.isoformat()}"
        return f"period={self.kind}&date={self.start.isoformat()}"

    # ── Trend buckets ──
    @property
    def bucket(self) -> str:
        if self.days <= 1:
            return "hour"
        if self.days <= 45:
            return "day"
        if self.days <= 120:
            return "week"
        return "month"

    def buckets(self):
        """[(bucket_start_date, label)] covering the period, in order."""
        out = []
        if self.bucket == "hour":
            return [(h, f"{h:02d}:00") for h in range(24)]
        if self.bucket == "day":
            d = self.start
            while d <= self.end:
                out.append((d, d.strftime("%d %b") if self.days > 7 else d.strftime("%a %d")))
                d += timedelta(days=1)
            return out
        if self.bucket == "week":
            d = self.start - timedelta(days=self.start.weekday())
            while d <= self.end:
                out.append((d, d.strftime("%d %b")))
                d += timedelta(weeks=1)
            return out
        d = date(self.start.year, self.start.month, 1)
        while d <= self.end:
            out.append((d, d.strftime("%b %Y") if self.days > 366 else d.strftime("%b")))
            d = date(d.year + (d.month // 12), d.month % 12 + 1, 1)
        return out

    def bucket_key(self, moment) -> object:
        """Bucket a datetime (aware) or date into its bucket key."""
        if isinstance(moment, datetime):
            local = timezone.localtime(moment)
            if self.bucket == "hour":
                return local.hour
            moment = local.date()
        if self.bucket == "day":
            return moment
        if self.bucket == "week":
            return moment - timedelta(days=moment.weekday())
        return date(moment.year, moment.month, 1)


def period_for(kind: str, anchor: date) -> Period:
    if kind == "day":
        return Period("day", anchor, anchor)
    if kind == "week":
        start = anchor - timedelta(days=anchor.weekday())  # Monday
        return Period("week", start, start + timedelta(days=6))
    if kind == "month":
        last = calendar.monthrange(anchor.year, anchor.month)[1]
        return Period("month", anchor.replace(day=1), anchor.replace(day=last))
    if kind == "quarter":
        first_month = (anchor.month - 1) // 3 * 3 + 1
        last_month = first_month + 2
        last = calendar.monthrange(anchor.year, last_month)[1]
        return Period("quarter", date(anchor.year, first_month, 1), date(anchor.year, last_month, last))
    if kind == "year":
        return Period("year", date(anchor.year, 1, 1), date(anchor.year, 12, 31))
    raise ValueError(f"Unknown period kind: {kind}")


def _parse_date(raw):
    try:
        return date.fromisoformat((raw or "").strip())
    except ValueError:
        return None


def period_from_request(request, default="month") -> Period:
    """Build a Period from ?period=…&date=… (or &start=…&end=… for custom)."""
    kind = request.GET.get("period", default)
    if kind not in PERIOD_KEYS:
        kind = default
    today = timezone.localdate()
    if kind == "custom":
        start = _parse_date(request.GET.get("start"))
        end = _parse_date(request.GET.get("end"))
        if start and end:
            if end < start:
                start, end = end, start
            if (end - start).days > MAX_CUSTOM_DAYS:
                start = end - timedelta(days=MAX_CUSTOM_DAYS)
            return Period("custom", start, end)
        kind = default
    anchor = _parse_date(request.GET.get("date")) or today
    return period_for(kind, anchor)
