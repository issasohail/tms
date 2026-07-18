from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


PROPOSER_DECLARATION = (
    "I, {{ proposer_name }}, holding CNIC No. {{ proposer_cnic }}, and having the relationship of "
    "{{ proposer_relationship }} with the Tenant, hereby declare that I personally know "
    "{{ tenant_name }}, holding CNIC No. {{ tenant_cnic }}.\n\n"
    "I understand that the Tenant is entering into a tenancy for {{ property_unit }}, for the period "
    "from {{ lease_start_date }} to {{ lease_end_date }}.\n\n"
    "Based on my personal knowledge of the Tenant's character, conduct, and financial responsibility, "
    "I believe that the Tenant is trustworthy, responsible, suitable for tenancy, and capable of paying "
    "the agreed rent, utility charges, and other lawful amounts on time. I recommend and vouch for the "
    "Tenant's suitability for this tenancy.\n\n"
    "If any dispute, misunderstanding, payment issue, complaint, or other matter arises between the Tenant "
    "and the Management/Landlord, I shall, when reasonably requested, be willing to assist in good faith in "
    "communicating with the parties and helping them reach an amicable resolution. I confirm that I am giving "
    "this declaration voluntarily and authorize the Management/Landlord to contact me for verification of my "
    "identity, relationship with the Tenant, and the information provided in this declaration. I understand "
    "that this declaration is a personal reference only and does not make me financially liable for the "
    "Tenant's obligations unless I separately sign a written guarantee."
)

SECONDER_DECLARATION = (
    "I, {{ seconder_name }}, holding CNIC No. {{ seconder_cnic }}, and having the relationship of "
    "{{ seconder_relationship }} with the Tenant, hereby declare that I personally know "
    "{{ tenant_name }}, holding CNIC No. {{ tenant_cnic }}.\n\n"
    "I understand that the Tenant is entering into a tenancy for {{ property_unit }}, for the period "
    "from {{ lease_start_date }} to {{ lease_end_date }}.\n\n"
    "Based on my personal knowledge of the Tenant's character, conduct, and financial responsibility, "
    "I believe that the Tenant is trustworthy, responsible, suitable for tenancy, and capable of paying "
    "the agreed rent, utility charges, and other lawful amounts on time. I support and second the proposal "
    "for this tenancy.\n\n"
    "If any dispute, misunderstanding, payment issue, complaint, or other matter arises between the Tenant "
    "and the Management/Landlord, I shall, when reasonably requested, be willing to assist in good faith in "
    "communicating with the parties and helping them reach an amicable resolution. I confirm that I am giving "
    "this declaration voluntarily and authorize the Management/Landlord to contact me for verification of my "
    "identity, relationship with the Tenant, and the information provided in this declaration. I understand "
    "that this declaration is a personal reference only and does not make me financially liable for the "
    "Tenant's obligations unless I separately sign a written guarantee."
)


def update_layout_and_declarations(apps, schema_editor):
    AgreementSignatureTemplate = apps.get_model(
        "leases", "AgreementSignatureTemplate"
    )
    AgreementSignatureTemplate.objects.update(
        legal_qr_reserve_width=Decimal("2.00"),
        legal_qr_reserve_height=Decimal("4.00"),
        proposer_declaration=PROPOSER_DECLARATION,
        seconder_declaration=SECONDER_DECLARATION,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("leases", "0089_set_qr_reserve_to_4x2"),
    ]

    operations = [
        migrations.AlterField(
            model_name="agreementsignaturetemplate",
            name="legal_qr_reserve_width",
            field=models.DecimalField(decimal_places=2, default=Decimal("2.00"), help_text="Width, in inches, of the QR/stamp reserve on the first Legal page.", max_digits=4, validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("7.50"))]),
        ),
        migrations.AlterField(
            model_name="agreementsignaturetemplate",
            name="legal_qr_reserve_height",
            field=models.DecimalField(decimal_places=2, default=Decimal("4.00"), help_text="Height, in inches, of the QR/stamp reserve on the first Legal page.", max_digits=4, validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("5.00"))]),
        ),
        migrations.AlterField(
            model_name="agreementsignaturetemplate",
            name="proposer_declaration",
            field=models.TextField(default=PROPOSER_DECLARATION),
        ),
        migrations.AlterField(
            model_name="agreementsignaturetemplate",
            name="seconder_declaration",
            field=models.TextField(default=SECONDER_DECLARATION),
        ),
        migrations.RunPython(update_layout_and_declarations, migrations.RunPython.noop),
    ]
