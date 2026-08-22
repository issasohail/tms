import django.db.models.deletion
from django.db import migrations, models


def windows_to_events(apps, schema_editor):
    Window = apps.get_model("smart_meter", "MeterTimingWindow")
    Event = apps.get_model("smart_meter", "MeterTimingEvent")
    seen = set()
    events = []
    for window in Window.objects.filter(is_enabled=True).order_by("meter_id", "weekday", "start_time", "end_time"):
        for event_time, command in ((window.start_time, "on"), (window.end_time, "off")):
            key = (window.meter_id, window.weekday, event_time)
            if key in seen:
                continue
            seen.add(key)
            events.append(Event(meter_id=window.meter_id, weekday=window.weekday, event_time=event_time, command=command))
    Event.objects.bulk_create(events, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [("smart_meter", "0022_meter_timing_window")]
    operations = [
        migrations.CreateModel(
            name="MeterTimingEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("weekday", models.PositiveSmallIntegerField(choices=[(0,"Monday"),(1,"Tuesday"),(2,"Wednesday"),(3,"Thursday"),(4,"Friday"),(5,"Saturday"),(6,"Sunday")])),
                ("event_time", models.TimeField()),
                ("command", models.CharField(choices=[("on","ON"),("off","OFF")], max_length=3)),
                ("notes", models.CharField(blank=True, max_length=160)),
                ("is_enabled", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("meter", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="timing_events", to="smart_meter.meter")),
            ],
            options={"ordering":["meter_id","weekday","event_time","id"]},
        ),
        migrations.AddConstraint(model_name="metertimingevent", constraint=models.UniqueConstraint(fields=("meter","weekday","event_time"), name="uniq_meter_timing_event")),
        migrations.AddIndex(model_name="metertimingevent", index=models.Index(fields=["meter","weekday","is_enabled"], name="sm_tevent_meter_day_idx")),
        migrations.RunPython(windows_to_events, migrations.RunPython.noop),
        migrations.DeleteModel(name="MeterTimingWindow"),
    ]
