from django.db import migrations


CLAUSE_5 = (
    "That the period of tenancy is hereby agreed as [LEASE_DURATION_MONTHS] months, "
    "commencing from [START_DATE] to [END_DATE], with a rent increase of @ "
    "[RENT_INCREASE_PERCENT]% after [LEASE_DURATION_MONTHS] months or the market "
    "rate, whichever is higher. Renewal is possible with mutual consent of both "
    "parties. To continue residing after [END_DATE], the Tenant must sign a new "
    "written agreement before that date. Without a new agreement, the Tenant shall "
    "vacate on or before [END_DATE]; continued occupation will be treated as "
    "unauthorized possession, subject to applicable law."
)

CLAUSE_8 = (
    "That the Tenant shall maintain the premises, including all fittings and "
    "fixtures, in good condition and replace with equal quality any item broken or "
    "damaged by the Tenant, occupants, guests, negligence, or misuse. Kitchen "
    "cabinets and wardrobes shall be kept clean, dry, ventilated, and protected from "
    "termites, excessive humidity, dampness, mold, and fungus. The Tenant shall "
    "promptly notify the Owner of any such condition and arrange necessary cleaning, "
    "drying, ventilation, termite spray or treatment, or anti-fungal treatment. The "
    "Tenant is responsible for damage caused or worsened by failure to take "
    "reasonable preventive action or give prompt notice, excluding documented "
    "pre-existing conditions, structural defects, hidden water leakage, and normal "
    "wear and tear. No alterations or wall drilling is allowed without written "
    "permission. Subletting is strictly prohibited."
)

CLAUSE_17 = (
    "That the Owner or the Owner's authorized representative may inspect the "
    "premises once every three months after giving the Tenant reasonable advance "
    "notice, except in an emergency. The Tenant shall provide reasonable access and "
    "promptly repair or replace, at the Tenant's expense, anything broken or damaged "
    "by the Tenant, occupants, guests, negligence, or misuse, excluding normal wear "
    "and tear and defects not caused by the Tenant."
)

OLD_CLAUSE_5 = {
    (
        "That the period of tenancy is hereby agreed as [LEASE_DURATION_MONTHS] "
        "months, commencing from [START_DATE] to [END_DATE], with a rent increase "
        "of @ [RENT_INCREASE_PERCENT]% after [LEASE_DURATION_MONTHS] months. Renewal "
        "is possible with mutual consent of both parties. The Tenant shall vacate "
        "peacefully after the lease expires."
    ),
    (
        "That the period of tenancy is hereby agreed as [LEASE_DURATION_MONTHS] "
        "months, commencing from [START_DATE] to [END_DATE], with a rent increase "
        "of @ [RENT_INCREASE_PERCENT]% after [LEASE_DURATION_MONTHS] months or the "
        "market rate whichever is higher. Renewal is possible with mutual consent "
        "of both parties. The Tenant shall vacate peacefully after the lease expires."
    ),
}

OLD_CLAUSE_8 = {
    (
        "That the Tenant shall maintain the premises in good condition, including "
        "all fittings and fixtures, and replace any broken items with equal quality. "
        "No alterations or wall drilling is allowed without written permission. "
        "Subletting is strictly prohibited."
    )
}

OLD_CLAUSE_17 = {
    "That the Owner may visit the premises with reasonable advance notice."
}


def update_current_clauses(apps, schema_editor):
    DefaultClause = apps.get_model("leases", "DefaultClause")
    Lease = apps.get_model("leases", "Lease")
    LeaseAgreementClause = apps.get_model("leases", "LeaseAgreementClause")
    LeaseRenewalClause = apps.get_model("leases", "LeaseRenewalClause")

    replacements = {
        5: (OLD_CLAUSE_5, CLAUSE_5),
        8: (OLD_CLAUSE_8, CLAUSE_8),
        17: (OLD_CLAUSE_17, CLAUSE_17),
    }

    for clause_number, (_old_texts, new_text) in replacements.items():
        DefaultClause.objects.update_or_create(
            clause_number=clause_number,
            is_active=True,
            defaults={"category": "general", "body": new_text},
        )

    active_leases = Lease.objects.filter(status="active")
    for lease in active_leases.iterator():
        for clause_number, (old_texts, new_text) in replacements.items():
            LeaseAgreementClause.objects.filter(
                lease_id=lease.pk,
                clause_number=clause_number,
                template_text__in=old_texts,
            ).update(template_text=new_text, is_customized=False)

        current_history = lease.renewals.order_by(
            "-renewal_number", "-id"
        ).first()
        if current_history is None:
            continue
        for clause_number, (old_texts, new_text) in replacements.items():
            LeaseRenewalClause.objects.filter(
                renewal_id=current_history.pk,
                clause_number=clause_number,
                template_text__in=old_texts,
            ).update(template_text=new_text, is_customized=False)


class Migration(migrations.Migration):

    dependencies = [
        ("leases", "0091_first_legal_page_standard_top_margin"),
    ]

    operations = [
        migrations.RunPython(
            update_current_clauses,
            migrations.RunPython.noop,
        ),
    ]
