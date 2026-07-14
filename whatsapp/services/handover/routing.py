from django.contrib.auth import get_user_model
from django.db.models import Q

from core.models import GlobalSettings
from whatsapp.models import WhatsAppStaffPropertyAccess, WhatsAppStaffRoutingRule


DEPARTMENT_SETTING_FIELDS = {
    "accounts": "whatsapp_accounts_staff",
    "maintenance": "whatsapp_maintenance_staff",
    "leasing": "whatsapp_leasing_staff",
    "management": "whatsapp_escalation_staff",
}


def eligible_staff(handover):
    """Return authorized staff in routing priority order without broadcasting globally."""
    users = []
    rules = (
        WhatsAppStaffRoutingRule.objects.select_related("staff_user")
        .filter(is_active=True, staff_user__is_active=True, staff_user__is_staff=True)
        .filter(Q(property=handover.property) | Q(property__isnull=True))
        .filter(Q(department=handover.department) | Q(department="general"))
        .order_by("priority", "-property_id", "id")
    )
    users.extend(rule.staff_user for rule in rules)

    if handover.property_id:
        users.extend(
            access.staff_user
            for access in WhatsAppStaffPropertyAccess.objects.select_related("staff_user")
            .filter(property=handover.property, is_active=True, staff_user__is_active=True, staff_user__is_staff=True)
            .order_by("id")
        )

    config = GlobalSettings.get_solo()
    department_field = DEPARTMENT_SETTING_FIELDS.get(handover.department)
    if department_field:
        users.append(getattr(config, department_field, None))
    users.append(config.whatsapp_default_support_staff)
    users.append(config.whatsapp_escalation_staff)

    User = get_user_model()
    users.extend(User.objects.filter(is_active=True, is_superuser=True).order_by("id")[:1])
    return _unique_authorized(users, handover.property)


def staff_can_access_handover(user, handover):
    if not user or not user.is_active or not user.is_staff:
        return False
    if user.is_superuser or handover.assigned_staff_id == user.pk:
        return True
    if not handover.property_id:
        return user in eligible_staff(handover)
    return WhatsAppStaffPropertyAccess.objects.filter(
        staff_user=user, property=handover.property, is_active=True
    ).exists() or user in eligible_staff(handover)


def _unique_authorized(users, property_obj):
    result = []
    seen = set()
    for user in users:
        if not user or user.pk in seen or not user.is_active or not user.is_staff:
            continue
        if property_obj and not user.is_superuser:
            has_property = WhatsAppStaffPropertyAccess.objects.filter(
                staff_user=user, property=property_obj, is_active=True
            ).exists()
            has_rule = WhatsAppStaffRoutingRule.objects.filter(
                staff_user=user, is_active=True
            ).filter(Q(property=property_obj) | Q(property__isnull=True)).exists()
            if not has_property and not has_rule:
                continue
        seen.add(user.pk)
        result.append(user)
    return result
