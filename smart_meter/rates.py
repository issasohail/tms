from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from core.models import GlobalSettings


@dataclass(frozen=True)
class ElectricityRateResolution:
    rate: Decimal
    source: str


def _positive_decimal(value):
    if value in (None, ""):
        return None
    try:
        value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return value if value >= 0 else None


def resolve_electricity_rate(*, lease=None, meter=None, unit=None, property_obj=None):
    """Resolve Global -> Property -> Unit -> Meter -> Lease overrides."""
    if unit is None:
        unit = getattr(meter, "unit", None) or getattr(lease, "unit", None)
    if property_obj is None and unit is not None:
        property_obj = getattr(unit, "property", None)

    settings_obj = GlobalSettings.get_solo()
    candidates = (
        ("Global", getattr(settings_obj, "unit_rate_per_kwh", None)),
        ("Property", getattr(property_obj, "electricity_unit_rate", None)),
        ("Unit", getattr(unit, "electricity_unit_rate", None)),
        ("Meter", getattr(meter, "unit_rate", None)),
        ("Lease", getattr(lease, "electric_unit_rate", None)),
    )
    resolution = ElectricityRateResolution(Decimal("0.0000"), "Global")
    for source, raw_value in candidates:
        value = _positive_decimal(raw_value)
        if value is not None:
            resolution = ElectricityRateResolution(value, source)
    return resolution
