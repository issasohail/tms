from decimal import Decimal

from django.db import migrations


def set_qr_reserve_to_4x2(apps, schema_editor):
    AgreementSignatureTemplate = apps.get_model(
        "leases", "AgreementSignatureTemplate"
    )
    AgreementSignatureTemplate.objects.update(
        legal_qr_reserve_width=Decimal("4.00"),
        legal_qr_reserve_height=Decimal("2.00"),
    )


class Migration(migrations.Migration):

    dependencies = [
        ("leases", "0088_agreementsignaturetemplate_legal_clause_spacing"),
    ]

    operations = [
        migrations.RunPython(set_qr_reserve_to_4x2, migrations.RunPython.noop),
    ]
