import re

from django.db import migrations


def classify_valid_meter_tariffs(apps, schema_editor):
    Meter = apps.get_model("smart_meter", "Meter")
    for meter in Meter.objects.all().only("pk", "meter_number").iterator():
        number = str(meter.meter_number or "").strip()
        if not re.fullmatch(r"\d{1,12}", number):
            capability = "unknown"
        elif number.startswith("26"):
            capability = "single_rate"
        else:
            capability = "multi_rate"
        Meter.objects.filter(pk=meter.pk).update(tariff_capability=capability)


def reset_capabilities(apps, schema_editor):
    apps.get_model("smart_meter", "Meter").objects.update(
        tariff_capability="unknown"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("smart_meter", "0036_meter_tariff_capability_and_more"),
    ]

    operations = [
        migrations.RunPython(classify_valid_meter_tariffs, reset_capabilities),
    ]
