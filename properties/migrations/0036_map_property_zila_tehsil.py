from django.db import migrations


def normalize_name(value):
    return " ".join(str(value or "").strip().casefold().split())


def map_property_locations(apps, schema_editor):
    Property = apps.get_model("properties", "Property")
    District = apps.get_model("punjab_estamp", "PunjabEStampDistrict")
    Tehsil = apps.get_model("punjab_estamp", "PunjabEStampTehsil")

    districts = {
        normalize_name(district.name): district
        for district in District.objects.all()
    }
    rawalpindi_district = District.objects.filter(portal_value="18").first()
    rawalpindi_tehsil = Tehsil.objects.filter(
        district=rawalpindi_district,
        portal_value="72",
    ).first()

    for property_obj in Property.objects.all().iterator():
        update_fields = []
        old_zila = normalize_name(property_obj.zila)
        matched_district = districts.get(old_zila)
        if not property_obj.zila_fk_id and matched_district:
            property_obj.zila_fk_id = matched_district.pk
            update_fields.append("zila_fk")

        effective_district_id = property_obj.zila_fk_id
        if (
            not property_obj.tehsil_id
            and rawalpindi_district
            and rawalpindi_tehsil
            and effective_district_id == rawalpindi_district.pk
        ):
            evidence_values = (
                property_obj.zila,
                property_obj.property_city,
                property_obj.property_address1,
                property_obj.property_address2,
                property_obj.house_no,
                property_obj.colony,
                property_obj.road,
            )
            has_rawalpindi_evidence = any(
                "rawalpindi" in normalize_name(value) for value in evidence_values
            )
            if has_rawalpindi_evidence:
                property_obj.tehsil_id = rawalpindi_tehsil.pk
                update_fields.append("tehsil")

        if update_fields:
            property_obj.save(update_fields=update_fields)


class Migration(migrations.Migration):

    dependencies = [
        ("properties", "0035_property_punjab_zila_tehsil_staging"),
        ("punjab_estamp", "0003_seed_verified_configuration"),
    ]

    operations = [
        migrations.RunPython(map_property_locations, migrations.RunPython.noop),
    ]

