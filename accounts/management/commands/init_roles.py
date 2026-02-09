from __future__ import annotations
from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission

ROLE_PERMS = {
    "Energy Viewers":   ["can_view_energy"],
    "Energy Exporters": ["can_view_energy", "can_export_energy"],
    "Operations":       ["can_view_energy", "can_export_energy", "can_manage_meters"],
    "Alert Senders":    ["can_send_whatsapp"],
}


class Command(BaseCommand):
    help = "Create default groups and attach permissions to them."

    def handle(self, *args, **opts):
        for role, codenames in ROLE_PERMS.items():
            group, _ = Group.objects.get_or_create(name=role)
            perms = Permission.objects.filter(codename__in=codenames)
            group.permissions.set(perms)
            self.stdout.write(self.style.SUCCESS(
                f"Configured group: {role} ({perms.count()} perms)"))
