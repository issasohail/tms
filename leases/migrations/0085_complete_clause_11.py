from django.db import migrations


SHOES_SENTENCE = (
    " Only shoes may be kept outside the Tenant's entrance door; all other "
    "belongings must remain inside the premises."
)


def _with_shoes(text):
    text = (text or "").rstrip()
    if "Only shoes may be kept outside" in text:
        return text
    return f"{text}{SHOES_SENTENCE}"


def complete_clause_11(apps, schema_editor):
    DefaultClause = apps.get_model("leases", "DefaultClause")
    LeaseAgreementClause = apps.get_model("leases", "LeaseAgreementClause")
    LeaseRenewalClause = apps.get_model("leases", "LeaseRenewalClause")

    for clause in DefaultClause.objects.filter(clause_number=11, is_active=True):
        updated = _with_shoes(clause.body)
        if updated != clause.body:
            clause.body = updated
            clause.save(update_fields=["body", "updated_at"])

    for clause in LeaseAgreementClause.objects.filter(
        clause_number=11, lease__status="active"
    ):
        updated = _with_shoes(clause.template_text)
        if updated != clause.template_text:
            clause.template_text = updated
            clause.save(update_fields=["template_text", "updated_at"])

    for clause in LeaseRenewalClause.objects.filter(
        clause_number=11, renewal__lease__status="active"
    ):
        updated = _with_shoes(clause.template_text)
        if updated != clause.template_text:
            clause.template_text = updated
            clause.save(update_fields=["template_text"])


class Migration(migrations.Migration):
    dependencies = [("leases", "0084_seed_parking_inventory_and_clauses")]
    operations = [migrations.RunPython(complete_clause_11, migrations.RunPython.noop)]
