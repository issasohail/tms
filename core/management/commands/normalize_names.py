from django.apps import apps
from django.core.management.base import BaseCommand

from core.utils.text import smart_title


MODEL_FIELD_GROUPS = [
    ("Tenants", "tenants", "Tenant", [
        "first_name",
        "last_name",
        "employer_name",
        "reference_name_1",
        "reference_name_2",
        "nationality",
        "city",
        "province",
        "country",
        "emergency_contact_name",
    ]),
    ("Tenant interest types", "tenants", "TenantInterestType", ["name"]),
    ("Properties", "properties", "Property", [
        "property_name",
        "owner_name",
        "owner_father_name",
        "caretaker_name",
        "caretaker_father_name",
        "property_city",
        "property_state",
    ]),
    ("Units", "properties", "Unit", []),
    ("Expense distributions", "properties", "ExpenseDistribution", ["name"]),
    ("Leases", "leases", "Lease", ["witness1_name", "witness2_name"]),
    ("Lease renewals", "leases", "LeaseRenewal", ["witness1_name", "witness2_name"]),
    ("Lease templates", "leases", "LeaseTemplate", ["name"]),
    ("Lease relationship types", "leases", "LeaseRelationshipType", ["name"]),
    ("Agreement placeholders", "leases", "AgreementPlaceholder", ["label", "category"]),
    ("WhatsApp templates", "leases", "WhatsAppTemplate", ["name"]),
    ("Lease document categories", "leases", "LeaseDocumentCategory", ["name"]),
    ("Invoice categories", "invoices", "ItemCategory", ["name"]),
    ("Payment methods", "core", "PaymentMethod", ["name"]),
    ("Global settings", "core", "GlobalSettings", ["site_name"]),
    ("Accounts", "accounts", "Account", ["first_name", "last_name"]),
    ("Maintenance requests", "maintenance", "MaintenanceRequest", ["title"]),
]

SUBMISSION_KEYS = [
    "first_name",
    "last_name",
    "employer_name",
    "reference_name_1",
    "reference_name_2",
    "nationality",
    "city",
    "province",
    "country",
    "emergency_contact_name",
]


class Command(BaseCommand):
    help = "Normalize existing human name/place fields with smart title casing."

    def handle(self, *args, **options):
        for label, app_label, model_name, fields in MODEL_FIELD_GROUPS:
            updated = self.normalize_model(app_label, model_name, fields)
            self.stdout.write(f"{label} updated: {updated}")

        submissions_updated = self.normalize_registration_submissions()
        self.stdout.write(f"Tenant registration submissions updated: {submissions_updated}")

    def normalize_model(self, app_label, model_name, fields):
        if not fields:
            return 0

        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            return 0

        model_field_names = {field.name for field in model._meta.fields}
        fields = [field for field in fields if field in model_field_names]
        if not fields:
            return 0

        updated = 0
        for obj in model.objects.all().iterator():
            dirty = []
            for field in fields:
                value = getattr(obj, field, None)
                normalized = smart_title(value)
                if normalized != value:
                    setattr(obj, field, normalized)
                    dirty.append(field)
            if dirty:
                obj.save(update_fields=dirty)
                updated += 1
        return updated

    def normalize_registration_submissions(self):
        try:
            model = apps.get_model("tenants", "TenantRegistrationSubmission")
        except LookupError:
            return 0

        updated = 0
        for submission in model.objects.all().iterator():
            data = submission.submitted_data or {}
            changed = False
            for key in SUBMISSION_KEYS:
                if key not in data:
                    continue
                normalized = smart_title(data[key])
                if normalized != data[key]:
                    data[key] = normalized
                    changed = True
            if changed:
                submission.submitted_data = data
                submission.save(update_fields=["submitted_data"])
                updated += 1
        return updated
