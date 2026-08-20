from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.db.models import Q


ALL_PROPERTIES_PERMISSION = "accounts.access_all_properties"

MODEL_PROPERTY_LOOKUPS = {
    "properties.property": ("",),
    "properties.unit": ("property",),
    "tenants.tenant": ("leases__unit__property",),
    "leases.lease": ("unit__property",),
    "payments.payment": ("lease__unit__property",),
    "payments.paymentdetail": ("payment__lease__unit__property",),
    "invoices.invoice": ("lease__unit__property",),
    "invoices.invoiceitem": ("invoice__lease__unit__property",),
    "expenses.expense": ("property",),
    "expenses.expensedistribution": ("expense__property",),
    "utilities.utility": ("property",),
    "maintenance.maintenancerequest": ("unit__property", "lease__unit__property"),
    "maintenance.maintenancerequestmedia": ("request__unit__property", "request__lease__unit__property"),
    "smart_meter.meter": ("unit__property",),
    "smart_meter.meterreading": ("meter__unit__property",),
    "smart_meter.bill": ("meter__unit__property",),
}


def has_all_property_access(user) -> bool:
    return bool(
        getattr(user, "is_authenticated", False)
        and (user.is_superuser or user.has_perm(ALL_PROPERTIES_PERMISSION))
    )


def allowed_property_ids(user):
    if not getattr(user, "is_authenticated", False):
        return set()
    if has_all_property_access(user):
        return None
    return set(user.property_access.values_list("property_id", flat=True))


def can_access_property(user, property_id) -> bool:
    if property_id in (None, ""):
        return False
    allowed = allowed_property_ids(user)
    return allowed is None or int(property_id) in allowed


def require_property_access(user, property_id):
    if not can_access_property(user, property_id):
        raise PermissionDenied("You do not have access to this property.")


def restrict_queryset_to_properties(queryset, user, lookup=None):
    allowed = allowed_property_ids(user)
    if allowed is None:
        return queryset
    if lookup is None:
        lookups = MODEL_PROPERTY_LOOKUPS.get(queryset.model._meta.label_lower)
    elif isinstance(lookup, str):
        lookups = (lookup,)
    else:
        lookups = tuple(lookup)
    if not lookups:
        return queryset
    condition = Q()
    for path in lookups:
        key = "pk__in" if path == "" else f"{path}_id__in"
        condition |= Q(**{key: allowed})
    return queryset.filter(condition).distinct()


def require_object_property_access(user, model, pk):
    queryset = restrict_queryset_to_properties(model._default_manager.all(), user)
    if not queryset.filter(pk=pk).exists():
        raise PermissionDenied("You do not have access to this property record.")


POSTED_SCOPE_FIELDS = {
    "property": "properties.Property",
    "property_id": "properties.Property",
    "unit": "properties.Unit",
    "unit_id": "properties.Unit",
    "lease": "leases.Lease",
    "lease_id": "leases.Lease",
    "invoice": "invoices.Invoice",
    "invoice_id": "invoices.Invoice",
    "meter": "smart_meter.Meter",
    "meter_id": "smart_meter.Meter",
    "expense": "expenses.Expense",
    "expense_id": "expenses.Expense",
}


def enforce_posted_property_scope(request, user):
    if user.is_superuser or request.method.upper() not in {"POST", "PUT", "PATCH"}:
        return
    from django.apps import apps

    for field_name, model_label in POSTED_SCOPE_FIELDS.items():
        raw_values = request.POST.getlist(field_name)
        for raw_value in raw_values:
            if not str(raw_value).isdigit():
                continue
            app_label, model_name = model_label.split(".", 1)
            model = apps.get_model(app_label, model_name)
            require_object_property_access(user, model, int(raw_value))
