# leases/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from leases.models import Lease, LeaseTemplate, LeaseAgreementClause


@receiver(post_save, sender=Lease)
def create_lease_clauses(sender, instance, created, **kwargs):
    if created:
        template = LeaseTemplate.objects.filter(is_default=True).first()
        if template:
            for i, clause in enumerate(template.clauses):
                LeaseAgreementClause.objects.create(
                    lease=instance,
                    clause_number=i+1,
                    template_text=clause
                )
