# invoices/widgets.py
from dal import autocomplete  # if you prefer django-autocomplete-light
# For django-select2:
from django_select2.forms import ModelSelect2Widget
from leases.models import Lease


class LeaseSelect2(ModelSelect2Widget):
    model = Lease
    search_fields = [
        "tenant__first_name__icontains",
        "tenant__last_name__icontains",
        "tenant__full_name__icontains",
        "unit__unit_number__icontains",
        "unit__property__name__icontains",
    ]
