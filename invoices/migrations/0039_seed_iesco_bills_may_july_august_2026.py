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


AUGUST_HISTORY = {
    "17146151548921": [
        ("Aug25", "623", "37215", "37215", True),
        ("Sep25", "858", "46150", "46150", True),
        ("Oct25", "611", "34186", "34186", True),
        ("Nov25", "53", "6002", "6002", True),
        ("Dec25", "202", "11779", "11779", True),
        ("Jan26", "818", "46071", "46071", True),
        ("Feb26", "923", "52245", "52245", True),
        ("Mar26", "87", "12627", "12627", True),
        ("Apr26", "246", "19430", "0", False),
        ("May26", "0", "24910", "24910", True),
        ("Jun26", "-451", "-2143", "0", False),
        ("Jul26", "28", "7904", "7904", True),
    ],
    "17146151548928": [
        ("Aug25", "RP 0", "2512", "2562", True),
        ("Sep25", "SS 0", "1178", "0", False),
        ("Oct25", "SS 0", "2458", "2458", True),
        ("Nov25", "0", "1180", "0", False),
        ("Dec25", "-68", "5244", "5244", True),
        ("Jan26", "457", "28655", "28655", True),
        ("Feb26", "574", "35680", "35680", True),
        ("Mar26", "129", "12606", "12606", True),
        ("Apr26", "0", "5035", "5035", True),
        ("May26", "74", "14094", "14094", True),
        ("Jun26", "770", "44428", "44428", True),
        ("Jul26", "418", "27425", "27425", True),
    ],
    "17146151547844": [
        ("Aug25", "0", "-50601", "0", False),
        ("Sep25", "-990", "-64937", "0", False),
        ("Oct25", "0", "-63757", "0", False),
        ("Nov25", "0", "-62577", "0", False),
        ("Dec25", "287", "-28604", "0", False),
        ("Jan26", "789", "19351", "19351", True),
        ("Feb26", "1140", "68704", "68704", True),
        ("Mar26", "-308", "321", "321", True),
        ("Apr26", "0", "6471", "6471", True),
        ("May26", "0", "5945", "5945", True),
        ("Jun26", "-2846", "-56028", "0", False),
        ("Jul26", "0", "-51248", "0", False),
    ],
}


def _history_rows(reference_no):
    return [
        {
            "month": month,
            "units": units,
            "bill": bill,
            "payment": payment,
            "paid": paid,
        }
        for month, units, bill, payment, paid in AUGUST_HISTORY.get(reference_no, [])
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

    bills = [
        {
            "reference_no": "17146151548921",
            "bill_month": "MAY 26",
            "consumer_id": "1143090754",
            "consumer_name": "IQBAL ISSA",
            "address": "ESSA BAHAI, PLOT NO.54 ST.NO.5A G.J.C.H.S RWP",
            "tariff_category": "A-1b(03)T",
            "meter_type": "3-P",
            "reading_date": "08 MAY 26",
            "issue_date": "09 MAY 26",
            "due_date": "20 MAY 26",
            "units": "399",
            "current_bill": "3,983",
            "arrears": "20,928",
            "grand_total": "24,910",
            "meter_readings": _registers("01312400010446", (
                ("11627.22", "11902.32", "275"),
                ("4481.76", "4605.90", "124"),
                ("9612.80", "10284.07", "671"),
                ("0.72", "0.72", "0"),
            )),
        },
        {
            "reference_no": "17146151548928",
            "bill_month": "MAY 26",
            "consumer_id": "1143243650",
            "consumer_name": "SOHAIL IQBAL ISSA",
            "address": "IQBAL JUMA ISSA, PLOT NO.F-57 ST.#.05 G.J.C.H.S RWP",
            "tariff_category": "A-1b(03)T",
            "meter_type": "3-P",
            "reading_date": "08 MAY 26",
            "issue_date": "09 MAY 26",
            "due_date": "20 MAY 26",
            "units": "658",
            "current_bill": "14,095",
            "arrears": "0",
            "grand_total": "14,094",
            "meter_readings": _registers("01322400009141", (
                ("1926.43", "2429.16", "503"),
                ("883.30", "1038.39", "155"),
                ("2039.00", "2302.58", "264"),
                ("0", "0", "0"),
            )),
        },
        {
            "reference_no": "17146151547844",
            "bill_month": "MAY 26",
            "consumer_id": "1143791551",
            "consumer_name": "KARIMA MANSOOR ALI JOMA",
            "address": "SHAMSUDDIN JOOMA, PLOT HO NO 09 ST 07 GOLDEN JUBILEE",
            "tariff_category": "A-1b(03)T",
            "meter_type": "3-P",
            "reading_date": "08 MAY 26",
            "issue_date": "09 MAY 26",
            "due_date": "20 MAY 26",
            "units": "615",
            "current_bill": "5,945",
            "arrears": "0",
            "grand_total": "5,945",
            "meter_readings": _registers("01312400010096", (
                ("13247.25", "13696.98", "450"),
                ("4782.76", "4947.38", "165"),
                ("18219.02", "20044.66", "1826"),
                ("2.13", "2.33", "0"),
            )),
        },
        {
            "reference_no": "17146151548921",
            "bill_month": "JUL 26",
            "consumer_id": "1143090754",
            "consumer_name": "Iqbal Issa",
            "address": "Essa Bahai, Plot No.54 St.No.5A G.J.C.H.S Rwp",
            "tariff_category": "A-1B(03)T",
            "meter_type": "3-P",
            "reading_date": "08 JUL 26",
            "issue_date": "09 JUL 26",
            "due_date": "23 JUL 26",
            "units": "656",
            "current_bill": "10,047",
            "arrears": "-2,143",
            "grand_total": "7,904",
            "meter_readings": _registers("01312400010446", (
                ("12221.75", "12681.97", "460"),
                ("4729.00", "4924.85", "196"),
                ("10904.98", "11532.86", "628"),
                ("0.90", "0.90", "0"),
            )),
        },
        {
            "reference_no": "17146151548928",
            "bill_month": "JUL 26",
            "consumer_id": "1143243650",
            "consumer_name": "Sohail Iqbal Issa",
            "address": "Iqbal Juma Issa, Plot No.F-57 St.#.05 G.J.C.H.S Rwp",
            "tariff_category": "A-1B(03)T",
            "meter_type": "3-P",
            "reading_date": "08 JUL 26",
            "issue_date": "09 JUL 26",
            "due_date": "23 JUL 26",
            "units": "894",
            "current_bill": "27,394",
            "arrears": "0",
            "grand_total": "27,425",
            "meter_readings": _registers("01322400009141", (
                ("3013.98", "3663.03", "649"),
                ("1223.73", "1469.06", "245"),
                ("2302.65", "2778.62", "476"),
                ("0", "0", "0"),
            )),
        },
        {
            "reference_no": "17146151547844",
            "bill_month": "JUL 26",
            "consumer_id": "1143791551",
            "consumer_name": "Karima Mansoor Ali Joma",
            "address": "Shamsuddin Jooma, Plot Ho No 09 St 07 Golden Jubilee",
            "tariff_category": "A-1B(03)T",
            "meter_type": "3-P",
            "reading_date": "08 JUL 26",
            "issue_date": "09 JUL 26",
            "due_date": "23 JUL 26",
            "units": "1100",
            "current_bill": "4,780",
            "arrears": "-56,028",
            "grand_total": "-51,248",
            "meter_readings": _registers("01312400010096", (
                ("14408.40", "15221.94", "814"),
                ("5195.56", "5481.21", "286"),
                ("21698.20", "23326.23", "1628"),
                ("3.05", "3.06", "0"),
            )),
        },
        {
            "reference_no": "17146151548921",
            "bill_month": "AUG 26",
            "consumer_id": "1143090754",
            "consumer_name": "Iqbal Issa",
            "address": "Essa Bahai, Plot No.54 St.No.5A G.J.C.H.S Rwp",
            "tariff_category": "A-1B(03)T",
            "meter_type": "3-P",
            "reading_date": "08 AUG 26",
            "issue_date": "09 AUG 26",
            "due_date": "24 AUG 26",
            "units": "603",
            "current_bill": "12,566",
            "arrears": "0",
            "grand_total": "12,566",
            "meter_readings": _registers("01312400010446", (
                ("12681.97", "13113.17", "431"),
                ("4924.85", "5097.07", "172"),
                ("11532.86", "12022.03", "489"),
                ("0.90", "0.90", "0"),
            )),
        },
        {
            "reference_no": "17146151548928",
            "bill_month": "AUG 26",
            "consumer_id": "1143243650",
            "consumer_name": "Sohail Iqbal Issa",
            "address": "Iqbal Juma Issa, Plot No.F-57 St.#.05 G.J.C.H.S Rwp",
            "tariff_category": "A-1B(03)T",
            "meter_type": "3-P",
            "reading_date": "08 AUG 26",
            "issue_date": "09 AUG 26",
            "due_date": "24 AUG 26",
            "units": "837",
            "current_bill": "18,429",
            "arrears": "0",
            "grand_total": "19,173",
            "meter_readings": _registers("01322400009141", (
                ("3663.03", "4272.10", "609"),
                ("1469.06", "1697.37", "228"),
                ("2778.62", "3356.85", "578"),
                ("0", "0", "0"),
            )),
        },
        {
            "reference_no": "17146151547844",
            "bill_month": "AUG 26",
            "consumer_id": "1143791551",
            "consumer_name": "Karima Mansoor Ali Joma",
            "address": "Shamsuddin Jooma, Plot Ho No 09 St 07 Golden Jubilee",
            "tariff_category": "A-1B(03)T",
            "meter_type": "3-P",
            "reading_date": "08 AUG 26",
            "issue_date": "09 AUG 26",
            "due_date": "24 AUG 26",
            "units": "1076",
            "current_bill": "5,470",
            "arrears": "-51,248",
            "grand_total": "-45,778",
            "meter_readings": _registers("01312400010096", (
                ("15221.94", "16021.29", "799"),
                ("5481.21", "5758.52", "277"),
                ("23326.23", "24623.61", "1297"),
                ("3.06", "3.07", "0"),
            )),
        },
    ]

    for bill in bills:
        values = dict(bill)
        reference_no = values.pop("reference_no")
        bill_month = values.pop("bill_month")
        created_defaults = {
            **values,
            "bill_history": (
                _history_rows(reference_no) if bill_month == "AUG 26" else []
            ),
            "current_month_paid": False,
            "trust_status": "parsed",
        }
        reading, created = IescoBillReading.objects.get_or_create(
            reference_no=reference_no,
            bill_month=bill_month,
            defaults=created_defaults,
        )
        if created:
            continue
        for field, value in values.items():
            setattr(reading, field, value)
        if bill_month == "AUG 26" and not reading.bill_history:
            reading.bill_history = _history_rows(reference_no)
        if reading.current_month_paid is None:
            reading.current_month_paid = False
        reading.trust_status = "parsed"
        reading.save()


class Migration(migrations.Migration):
    dependencies = [
        ("invoices", "0038_utility_bills"),
    ]

    operations = [
        migrations.RunPython(seed_iesco_bills, migrations.RunPython.noop),
    ]
