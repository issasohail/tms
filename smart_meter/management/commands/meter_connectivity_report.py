from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Prefetch
from django.utils import timezone

from smart_meter.models import Meter, MeterRawFrame
from smart_meter.status import (
    online_threshold_minutes,
    resolve_meter_online_statuses,
)


def _format_dt(value):
    return timezone.localtime(value).isoformat(timespec="seconds") if value else "-"


def _format_age(value, now):
    if not value:
        return "-"
    seconds = max(0, int((now - value).total_seconds()))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}h {minutes}m {seconds}s"


def _gap_metrics(timestamps, start, end, threshold):
    samples = sorted(ts for ts in timestamps if start - threshold <= ts <= end)
    in_window_count = sum(1 for ts in samples if ts >= start)
    intervals = []
    for ts in samples:
        interval_start = max(start, ts)
        interval_end = min(end, ts + threshold)
        if interval_end > start and interval_start < end:
            intervals.append((interval_start, interval_end))

    covered_seconds = 0.0
    if intervals:
        current_start, current_end = intervals[0]
        for interval_start, interval_end in intervals[1:]:
            if interval_start <= current_end:
                current_end = max(current_end, interval_end)
            else:
                covered_seconds += (current_end - current_start).total_seconds()
                current_start, current_end = interval_start, interval_end
        covered_seconds += (current_end - current_start).total_seconds()

    window_seconds = max(1.0, (end - start).total_seconds())
    offline_percentage = max(0.0, 100.0 * (1.0 - covered_seconds / window_seconds))

    points = [start] + [ts for ts in samples if start <= ts <= end] + [end]
    raw_gaps = [later - earlier for earlier, later in zip(points, points[1:])]
    long_gaps = [gap for gap in raw_gaps if gap > threshold]
    longest_gap = max(raw_gaps, default=timedelta(0))
    return in_window_count, len(long_gaps), longest_gap, offline_percentage


class Command(BaseCommand):
    help = "Read-only smart-meter contact and measurement freshness report."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=float, default=48.0)

    def handle(self, *args, **options):
        hours = options["hours"]
        if hours <= 0:
            raise CommandError("--hours must be greater than zero")

        now = timezone.now()
        start = now - timedelta(hours=hours)
        threshold = timedelta(minutes=online_threshold_minutes())
        frames = MeterRawFrame.objects.filter(
            received_at__gte=start - threshold,
            received_at__lte=now,
        ).only("meter_id", "received_at")
        meters = list(
            Meter.objects.filter(is_active=True)
            .select_related("live")
            .prefetch_related(Prefetch("raw_frames", queryset=frames, to_attr="report_frames"))
            .order_by("meter_number")
        )
        statuses = resolve_meter_online_statuses(
            (meter, getattr(meter, "live", None)) for meter in meters
        )

        headings = (
            "Meter",
            "Name",
            "State",
            "Last contact",
            "Last measurement",
            "Current age",
            "Valid frames",
            "Gaps",
            "Longest gap",
            "Offline %",
        )
        self.stdout.write(" | ".join(headings))
        for meter in meters:
            status = statuses[meter.pk]
            timestamps = [frame.received_at for frame in meter.report_frames]
            count, gap_count, longest, offline_pct = _gap_metrics(
                timestamps, start, now, threshold
            )
            longest_seconds = max(0, int(longest.total_seconds()))
            longest_hours, remainder = divmod(longest_seconds, 3600)
            longest_minutes, _seconds = divmod(remainder, 60)
            self.stdout.write(
                " | ".join(
                    (
                        meter.meter_number,
                        meter.name or "-",
                        status["connection_state"],
                        _format_dt(status["last_contact_at"]),
                        _format_dt(status["last_measurement_at"]),
                        _format_age(status["last_measurement_at"], now),
                        str(count),
                        str(gap_count),
                        f"{longest_hours}h {longest_minutes}m",
                        f"{offline_pct:.1f}",
                    )
                )
            )

