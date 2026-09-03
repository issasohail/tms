from django.db.models import CharField, F, IntegerField, OuterRef, Prefetch, Q, Subquery
from django.db.models.functions import Coalesce

from leases.models import LeaseUnitOccupancy


PREFETCH_ATTR = "_invoice_unit_history"


def _matching_occupancies():
    return (
        LeaseUnitOccupancy.objects
        .filter(
            lease_id=OuterRef("lease_id"),
            move_in_date__lte=OuterRef("issue_date"),
        )
        .filter(Q(move_out_date__isnull=True) | Q(move_out_date__gte=OuterRef("issue_date")))
        .order_by("-move_in_date", "-id")
    )


def annotate_historical_invoice_units(queryset):
    """Annotate invoice rows with their issue-date unit, falling back to lease.unit."""
    matching = _matching_occupancies()
    return queryset.annotate(
        historical_unit_id=Coalesce(
            Subquery(matching.values("unit_id")[:1]),
            F("lease__unit_id"),
            output_field=IntegerField(),
        ),
        historical_unit_number=Coalesce(
            Subquery(matching.values("unit__unit_number")[:1]),
            F("lease__unit__unit_number"),
            output_field=CharField(),
        ),
        historical_property_id=Coalesce(
            Subquery(matching.values("unit__property_id")[:1]),
            F("lease__unit__property_id"),
            output_field=IntegerField(),
        ),
        historical_property_name=Coalesce(
            Subquery(matching.values("unit__property__property_name")[:1]),
            F("lease__unit__property__property_name"),
            output_field=CharField(),
        ),
    )


def historical_unit_prefetch(prefix=""):
    """Prefetch lease occupancy units for Invoice objects, including nested relations."""
    return Prefetch(
        f"{prefix}lease__unit_occupancies",
        queryset=(
            LeaseUnitOccupancy.objects
            .select_related("unit", "unit__property")
            .order_by("-move_in_date", "-id")
        ),
        to_attr=PREFETCH_ATTR,
    )


def prepare_historical_invoice_units(queryset):
    """Prepare an Invoice queryset for historical filtering, sorting, and display."""
    return annotate_historical_invoice_units(queryset).prefetch_related(
        historical_unit_prefetch()
    )


def resolve_historical_invoice_unit(invoice):
    """Return the unit occupied on invoice.issue_date, or the lease's current unit."""
    cached = getattr(invoice, "_historical_unit_cache", None)
    if cached is not None:
        return cached

    lease = getattr(invoice, "lease", None)
    fallback = getattr(lease, "unit", None) if lease else None
    issue_date = getattr(invoice, "issue_date", None)
    if not lease or not issue_date:
        return fallback

    occupancies = getattr(lease, PREFETCH_ATTR, None)
    if occupancies is None:
        occupancies = (
            LeaseUnitOccupancy.objects
            .filter(lease_id=lease.pk, move_in_date__lte=issue_date)
            .filter(Q(move_out_date__isnull=True) | Q(move_out_date__gte=issue_date))
            .select_related("unit", "unit__property")
            .order_by("-move_in_date", "-id")
        )

    occupancy = next(
        (
            occupancy
            for occupancy in occupancies
            if occupancy.move_in_date <= issue_date
            and (occupancy.move_out_date is None or occupancy.move_out_date >= issue_date)
        ),
        None,
    )
    unit = occupancy.unit if occupancy else fallback
    if unit is not None:
        invoice._historical_unit_cache = unit
    return unit
