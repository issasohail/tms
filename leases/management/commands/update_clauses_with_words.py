from django.core.management.base import BaseCommand
from leases.models import LeaseAgreementClause


class Command(BaseCommand):
    help = "Ensure clauses using [AMOUNT] also include [AMOUNT_IN_WORDS]"

    def handle(self, *args, **kwargs):
        updated = 0
        mappings = {
            "[MONTHLY_RENT]": "[MONTHLY_RENT_IN_WORDS]",
            "[SECURITY_DEPOSIT]": "[SECURITY_DEPOSIT_IN_WORDS]",
            "[TOTAL_MONTHLY]": "[TOTAL_MONTHLY_IN_WORDS]",
        }

        for clause in LeaseAgreementClause.objects.all():
            updated_text = clause.template_text
            changed = False

            for key, in_words in mappings.items():
                if key in updated_text and in_words not in updated_text:
                    updated_text += f"\n\n({in_words})"
                    changed = True

            if changed:
                clause.template_text = updated_text
                clause.save()
                updated += 1

        self.stdout.write(self.style.SUCCESS(
            f"Updated {updated} clauses with missing _IN_WORDS placeholders."))
