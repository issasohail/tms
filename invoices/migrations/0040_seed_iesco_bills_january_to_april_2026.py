from django.db import migrations


def _registers(meter_no, values):
    labels = (
        ("import", "off_peak"),
        ("import", "peak"),
        ("export", "off_peak"),
        ("export", "peak"),
    )
    return [
        {
            "direction": direction,
            "period": period,
            "meter_no": meter_no,
            "multiplier": "1",
            "previous": previous,
            "present": present,
            "units": units,
        }
        for (direction, period), (previous, present, units) in zip(labels, values)
    ]


def seed_iesco_bills(apps, schema_editor):
    IescoBillReading = apps.get_model("invoices", "IescoBillReading")
    IescoStandaloneMeter = apps.get_model("invoices", "IescoStandaloneMeter")
    Unit = apps.get_model("properties", "Unit")

    sources = {
        "17146151548921": "F54-SOLAR",
        "17146151548928": "F56-SOLAR",
        "17146151547844": "H9",
    }
    for reference_no, description in sources.items():
        if not Unit.objects.filter(electric_meter_num=reference_no).exists():
            IescoStandaloneMeter.objects.get_or_create(
                reference_no=reference_no,
                defaults={"description": description, "is_active": True},
            )

    identities = {
        "17146151548921": {
            "consumer_id": "1143090754",
            "consumer_name": "Iqbal Issa",
            "address": "Essa Bahai, Plot No.54 St.No.5A G.J.C.H.S Rwp",
            "meter_no": "01312400010446",
        },
        "17146151548928": {
            "consumer_id": "1143243650",
            "consumer_name": "Sohail Iqbal Issa",
            "address": "Iqbal Juma Issa, Plot No.F-57 St.#.05 G.J.C.H.S Rwp",
            "meter_no": "01322400009141",
        },
        "17146151547844": {
            "consumer_id": "1143791551",
            "consumer_name": "Karima Mansoor Ali Joma",
            "address": "Shamsuddin Jooma, Plot Ho No 09 St 07 Golden Jubilee",
            "meter_no": "01312400010096",
        },
    }

    bills = [
        ("17146151548921", "JAN 26", "08 JAN 26", "09 JAN 26", "22 JAN 26",
         "969", "46,131", "0", "46,071", False,
         (("9433.70", "10164.70", "731"), ("3638.67", "3876.24", "238"),
          ("8651.48", "8802.22", "151"), ("0.53", "0.53", "0"))),
        ("17146151548928", "JAN 26", "08 JAN 26", "09 JAN 26", "22 JAN 26",
         "581", "28,655", "0", "28,655", False,
         (("419.39", "819.13", "400"), ("195.62", "376.71", "181"),
          ("682.90", "807.36", "124"), ("0", "0", "0"))),
        ("17146151547844", "JAN 26", "08 JAN 26", "09 JAN 26", "22 JAN 26",
         "1062", "47,955", "-28,604", "19,351", False,
         (("10555.02", "11377.79", "823"), ("3948.85", "4187.99", "239"),
          ("15374.69", "15647.76", "273"), ("1.64", "1.78", "0"))),
        ("17146151548921", "FEB 26", "08 FEB 26", "09 FEB 26", "20 FEB 26",
         "1139", "52,176", "0", "52,245", True,
         (("10164.70", "10998.84", "834"), ("3876.24", "4181.23", "305"),
          ("8802.22", "9017.92", "216"), ("0.53", "0.53", "0"))),
        ("17146151548928", "FEB 26", "08 FEB 26", "09 FEB 26", "20 FEB 26",
         "729", "35,680", "0", "35,680", True,
         (("819.13", "1318.79", "500"), ("376.71", "605.51", "229"),
          ("807.36", "962.73", "155"), ("0", "0", "0"))),
        ("17146151548921", "MAR 26", "08 MAR 26", "09 MAR 26", "24 MAR 26",
         "437", "11,033", "0", "12,627", False,
         (("10998.84", "11289.13", "290"), ("4181.23", "4328.68", "147"),
          ("9017.92", "9367.91", "350"), ("0.53", "0.72", "0"))),
        ("17146151548928", "MAR 26", "08 MAR 26", "09 MAR 26", "24 MAR 26",
         "484", "11,648", "0", "12,606", False,
         (("1318.79", "1662.35", "344"), ("605.51", "745.77", "140"),
          ("962.73", "1317.37", "355"), ("0", "0", "0"))),
        ("17146151547844", "MAR 26", "08 MAR 26", "09 MAR 26", "24 MAR 26",
         "508", "-1,332", "0", "321", False,
         (("12545.24", "12914.22", "369"), ("4517.76", "4656.76", "139"),
          ("16004.86", "16820.66", "816"), ("1.82", "1.95", "0"))),
        ("17146151548921", "APR 26", "08 APR 26", "09 APR 26", "22 APR 26",
         "491", "17,856", "0", "19,430", False,
         (("11289.13", "11627.22", "338"), ("4328.68", "4481.76", "153"),
          ("9367.91", "9612.80", "245"), ("0.72", "0.72", "0"))),
        ("17146151548928", "APR 26", "08 APR 26", "09 APR 26", "22 APR 26",
         "402", "3,983", "0", "5,035", False,
         (("1662.35", "1926.43", "264"), ("745.77", "883.30", "138"),
          ("1317.37", "2039.00", "722"), ("0", "0", "0"))),
        ("17146151547844", "APR 26", "08 APR 26", "09 APR 26", "22 APR 26",
         "459", "4,380", "0", "6,471", False,
         (("12914.22", "13247.25", "333"), ("4656.76", "4782.76", "126"),
          ("16820.66", "18219.02", "1398"), ("1.95", "2.13", "0"))),
    ]

    for (
        reference_no, bill_month, reading_date, issue_date, due_date,
        units, current_bill, arrears, grand_total, paid, register_values,
    ) in bills:
        identity = identities[reference_no]
        values = {
            "consumer_id": identity["consumer_id"],
            "consumer_name": identity["consumer_name"],
            "address": identity["address"],
            "tariff_category": "A-1b(03)T",
            "meter_type": "3-P",
            "reading_date": reading_date,
            "issue_date": issue_date,
            "due_date": due_date,
            "units": units,
            "current_bill": current_bill,
            "arrears": arrears,
            "grand_total": grand_total,
            "meter_readings": _registers(identity["meter_no"], register_values),
            "trust_status": "parsed",
        }
        if paid:
            values.update({
                "amount_paid": grand_total,
                "payment_date": "12 FEB 26",
                "current_month_paid": True,
            })

        reading, created = IescoBillReading.objects.get_or_create(
            reference_no=reference_no,
            bill_month=bill_month,
            defaults={
                **values,
                "bill_history": [],
                "current_month_paid": paid,
            },
        )
        if created:
            continue

        for field, value in values.items():
            setattr(reading, field, value)
        if reading.current_month_paid is None:
            reading.current_month_paid = paid
        reading.save()


class Migration(migrations.Migration):
    dependencies = [
        ("invoices", "0039_seed_iesco_bills_may_july_august_2026"),
    ]

    operations = [
        migrations.RunPython(seed_iesco_bills, migrations.RunPython.noop),
    ]
