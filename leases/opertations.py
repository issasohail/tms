# operations.py
from django.db import transaction


@transaction.atomic
def bulk_update_placeholder_default(placeholder_name, new_default):
    """
    Updates default value across all templates and clauses
    """
    # Update registry
    registry = PlaceholderRegistry.objects.get(name=placeholder_name)
    registry.default_value = new_default
    registry.save()

    # Update all non-customized clauses
    clauses = LeaseAgreementClause.objects.filter(
        template_text__contains=f'[{placeholder_name}]',
        is_customized=False
    )

    for clause in clauses:
        clause.template_text = clause.template_text.replace(
            f'[{placeholder_name}]',
            new_default
        )
        clause.save()


@transaction.atomic
def bulk_apply_template_to_leases(template_id, lease_ids):
    """
    Applies template to multiple leases while preserving customizations
    """
    template = LeaseTemplate.objects.get(id=template_id)

    for lease_id in lease_ids:
        lease = Lease.objects.get(id=lease_id)
        existing_clauses = {c.clause_number: c for c in lease.clauses.all()}

        for num, text in template.clauses.items():
            if num in existing_clauses:
                clause = existing_clauses[num]
                if not clause.is_customized:
                    clause.template_text = text
                    clause.save()
            else:
                LeaseAgreementClause.objects.create(
                    lease=lease,
                    clause_number=num,
                    template_text=text
                )


@transaction.atomic
def bulk_refresh_agreements(lease_ids):
    """
    Forces regeneration of agreements with current data
    """
    for lease in Lease.objects.filter(id__in=lease_ids):
        generate_lease_agreement(lease)  # Your PDF generation function
