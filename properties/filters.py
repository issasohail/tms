# properties/filters.py

import django_filters
from .models import Unit, Property


class UnitFilter(django_filters.FilterSet):
    property = django_filters.ModelChoiceFilter(
        queryset=Property.objects.all(),
        label="Property",
        empty_label="All Properties"
    )

    class Meta:
        model = Unit
        fields = ['property']
