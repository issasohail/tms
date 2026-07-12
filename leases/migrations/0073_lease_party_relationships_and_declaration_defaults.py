from django.db import migrations, models
import django.db.models.deletion

PROPOSER_TEXT = (
    "I, {{ proposer_name }}, holding CNIC No. {{ proposer_cnic }}, and having the relationship of "
    "{{ proposer_relationship }} with the Tenant, hereby declare that I personally know {{ tenant_name }}, "
    "holding CNIC No. {{ tenant_cnic }}. I understand that the Tenant is entering into a tenancy for "
    "{{ property_unit }} under Lease No. {{ lease_number }}, for the period from {{ lease_start_date }} to "
    "{{ lease_end_date }}. Based on my personal knowledge of the Tenant's character, conduct, and financial "
    "responsibility, I believe that the Tenant is trustworthy, responsible, suitable for tenancy, and capable "
    "of paying the agreed rent, utility charges, and other lawful amounts on time. I recommend and vouch for "
    "the Tenant's suitability for this tenancy. If any dispute, misunderstanding, payment issue, complaint, or "
    "other matter arises between the Tenant and the Management/Landlord, I shall, when reasonably requested, "
    "be willing to assist in good faith in communicating with the parties and helping them reach an amicable "
    "resolution. I confirm that I am giving this declaration voluntarily and authorize the Management/Landlord "
    "to contact me for verification of my identity, relationship with the Tenant, and the information provided "
    "in this declaration. I understand that this declaration is a personal reference only and does not make me "
    "financially liable for the Tenant's obligations unless I separately sign a written guarantee."
)

SECONDER_TEXT = (
    "I, {{ seconder_name }}, holding CNIC No. {{ seconder_cnic }}, and having the relationship of "
    "{{ seconder_relationship }} with the Tenant, hereby declare that I personally know {{ tenant_name }}, "
    "holding CNIC No. {{ tenant_cnic }}. I understand that the Tenant is entering into a tenancy for "
    "{{ property_unit }} under Lease No. {{ lease_number }}, for the period from {{ lease_start_date }} to "
    "{{ lease_end_date }}. I support and second the proposal for this tenancy. Based on my personal knowledge "
    "of the Tenant's character, conduct, and financial responsibility, I believe that the Tenant is trustworthy, "
    "responsible, suitable for tenancy, and capable of paying the agreed rent, utility charges, and other lawful "
    "amounts on time. If any dispute, misunderstanding, payment issue, complaint, or other matter arises between "
    "the Tenant and the Management/Landlord, I shall, when reasonably requested, be willing to assist in good "
    "faith in communicating with the parties and helping them reach an amicable resolution. I confirm that I am "
    "giving this declaration voluntarily and authorize the Management/Landlord to contact me for verification of "
    "my identity, relationship with the Tenant, and the information provided in this declaration. I understand "
    "that this declaration is a personal reference only and does not make me financially liable for the Tenant's "
    "obligations unless I separately sign a written guarantee."
)


def forwards(apps, schema_editor):
    Template = apps.get_model("leases", "AgreementSignatureTemplate")
    Template.objects.filter(is_active=True).update(
        heading="Proposer and Seconder Declaration",
        proposer_declaration=PROPOSER_TEXT,
        seconder_declaration=SECONDER_TEXT,
        show_phone=True,
        show_address=False,
        show_thumb_impression=False,
    )


def backwards(apps, schema_editor):
    Template = apps.get_model("leases", "AgreementSignatureTemplate")
    Template.objects.filter(is_active=True).update(
        heading="Proposer, Seconder and Witness Signatures",
        proposer_declaration="I recommend the applicant for tenancy and confirm the information stated below.",
        seconder_declaration="I support the proposal for tenancy and confirm the information stated below.",
    )


class Migration(migrations.Migration):
    dependencies = [("leases", "0072_agreement_signature_template")]
    operations = [
        migrations.AddField(
            model_name="lease",
            name="proposer_relationship",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="proposer_leases", to="leases.leaserelationshiptype"),
        ),
        migrations.AddField(
            model_name="lease",
            name="seconder_relationship",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="seconder_leases", to="leases.leaserelationshiptype"),
        ),
        migrations.RunPython(forwards, backwards),
    ]
