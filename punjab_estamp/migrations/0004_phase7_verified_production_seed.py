from django.db import migrations


DISTRICTS = (
    ("Attock", "19"),
    ("Bahawalnagar", "16"),
    ("Bahawalpur", "15"),
    ("Bhakkar", "32"),
    ("Chakwal", "21"),
    ("Chiniot", "14"),
    ("Dera Ghazi Khan", "26"),
    ("Faisalabad", "11"),
    ("Gujranwala", "1"),
    ("Gujrat", "2"),
    ("Hafizabad", "5"),
    ("Jhelum", "20"),
    ("Jhang", "13"),
    ("Kasur", "8"),
    ("Khanewal", "25"),
    ("Khushab", "33"),
    ("Kot Addu", "38"),
    ("Lahore", "7"),
    ("Layyah", "27"),
    ("Lodhran", "24"),
    ("Mandi Bahauddin", "6"),
    ("Mianwali", "31"),
    ("Multan", "22"),
    ("Murree", "40"),
    ("Muzaffargarh", "29"),
    ("Nankana Sahib", "9"),
    ("Narowal", "4"),
    ("Okara", "35"),
    ("Pakpattan", "36"),
    ("Rahim Yar Khan", "17"),
    ("Rajanpur", "28"),
    ("Rawalpindi", "18"),
    ("Sahiwal", "34"),
    ("Sargodha", "30"),
    ("Sheikhupura", "10"),
    ("Sialkot", "3"),
    ("Talagang", "41"),
    ("Taunsa", "42"),
    ("Toba Tek Singh", "12"),
    ("Vehari", "23"),
    ("Wazirabad", "39"),
)

RAWALPINDI_TEHSILS = (
    ("Gujar Khan", "80"),
    ("Kahuta", "82"),
    ("Kallar Syedan", "84"),
    ("Rawalpindi", "72"),
    ("Rawalpindi Cantt", "176"),
    ("Rawalpindi Saddar", "177"),
    ("Taxila", "83"),
)

RELATIONS = (
    ("F/O", "12"),
    ("M/O", "20"),
    ("S/O", "33"),
    ("D/O", "34"),
    ("H/O", "35"),
    ("W/O", "36"),
    ("Widow of", "37"),
    ("Guardian", "38"),
    ("Representative From", "39"),
)

DEFAULT_PURPOSE = {
    "name": "AGREEMENT OR MEMORANDUM OF AN AGREEMENT - 5(ccc)",
    "portal_value": "208",
    "denomination": 100,
    "default_continuation_sheets": 1,
}


def seed_verified_production_cache(apps, schema_editor):
    District = apps.get_model("punjab_estamp", "PunjabEStampDistrict")
    Tehsil = apps.get_model("punjab_estamp", "PunjabEStampTehsil")
    Relation = apps.get_model("punjab_estamp", "PunjabEStampRelation")
    Purpose = apps.get_model("punjab_estamp", "PunjabEStampPurpose")

    districts_by_portal_value = {}
    for sort_order, (name, portal_value) in enumerate(DISTRICTS, start=1):
        district, _created = District.objects.get_or_create(
            portal_value=portal_value,
            defaults={
                "name": name,
                "active": True,
                "sort_order": sort_order,
            },
        )
        districts_by_portal_value[portal_value] = district

    rawalpindi = districts_by_portal_value["18"]
    for sort_order, (name, portal_value) in enumerate(RAWALPINDI_TEHSILS, start=1):
        Tehsil.objects.get_or_create(
            district=rawalpindi,
            portal_value=portal_value,
            defaults={
                "name": name,
                "active": True,
                "sort_order": sort_order,
            },
        )

    for sort_order, (name, portal_value) in enumerate(RELATIONS, start=1):
        Relation.objects.get_or_create(
            portal_value=portal_value,
            defaults={
                "name": name,
                "active": True,
                "sort_order": sort_order,
            },
        )

    Purpose.objects.get_or_create(
        portal_value=DEFAULT_PURPOSE["portal_value"],
        defaults={
            "name": DEFAULT_PURPOSE["name"],
            "denomination": DEFAULT_PURPOSE["denomination"],
            "default_continuation_sheets": DEFAULT_PURPOSE[
                "default_continuation_sheets"
            ],
            "active": True,
            "is_default_for_lease": True,
            "sort_order": 1,
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("punjab_estamp", "0003_seed_verified_configuration"),
    ]

    operations = [
        migrations.RunPython(
            seed_verified_production_cache,
            migrations.RunPython.noop,
        ),
    ]
