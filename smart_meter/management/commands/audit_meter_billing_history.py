"""Read-only smart-meter installation/billing audit.

This command deliberately has no mutation flag. It is intended to be run before
any corrective database work and to produce a reviewable report.
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from invoices.models import Invoice
from smart_meter.models import Meter, MeterInstallation


class Command(BaseCommand):
    help = (
        "Read-only audit of meter installation history, cached assignments, and "
        "invoice meter references. This command never writes to the database."
    )

    def add_arguments(self, parser):
        parser.add_argument("--meter", action="append", dest="meters", default=[])
        parser.add_argument("--unit-id", type=int)
        parser.add_argument("--lease-id", type=int)
        parser.add_argument("--invoice-number", action="append", dest="invoice_numbers", default=[])

    def handle(self, *args, **options):
        meters = Meter.objects.all().order_by("meter_number")
        if options["meters"]:
            meters = meters.filter(meter_number__in=options["meters"])
        if options["unit_id"]:
            meters = meters.filter(
                Q(unit_id=options["unit_id"]) | Q(installations__unit_id=options["unit_id"])
            ).distinct()
        if options["lease_id"]:
            meters = meters.filter(installations__lease_id=options["lease_id"]).distinct()

        if not meters.exists() and not options["invoice_numbers"]:
            raise CommandError("No matching meters or invoices found.")

        self.stdout.write("SMART-METER BILLING / HISTORY AUDIT (READ ONLY)")
        self.stdout.write("No database rows will be changed.\n")

        for meter in meters:
            self.stdout.write(f"METER {meter.meter_number} (id={meter.pk})")
            self.stdout.write(f"  cached current unit: {meter.unit_id or '-'}")
            rows = list(
                MeterInstallation.objects.filter(meter=meter)
                .select_related("unit", "lease", "lease__tenant")
                .order_by("start_date", "id")
            )
            active = [r for r in rows if r.is_active and r.end_date is None]
            self.stdout.write(f"  installations: {len(rows)}; active: {len(active)}")
            if len(active) > 1:
                self.stdout.write(self.style.ERROR("  ERROR: more than one active installation"))
            if active and meter.unit_id != active[0].unit_id:
                self.stdout.write(self.style.WARNING(
                    f"  WARNING: cached unit {meter.unit_id} != active installation unit {active[0].unit_id}"
                ))

            previous = None
            for row in rows:
                tenant = row.lease.tenant if row.lease_id and row.lease else None
                self.stdout.write(
                    "  - installation id={id} unit={unit} lease={lease} tenant={tenant} "
                    "from={start} to={end} start_kwh={start_kwh} end_kwh={end_kwh} active={active}".format(
                        id=row.pk,
                        unit=row.unit_id,
                        lease=row.lease_id or "-",
                        tenant=tenant or "-",
                        start=row.start_date,
                        end=row.end_date or "current",
                        start_kwh=row.start_reading,
                        end_kwh=row.end_reading if row.end_reading is not None else "-",
                        active=row.is_active,
                    )
                )
                if previous is not None and previous.end_date is not None:
                    # Same-day handoff is valid for a physical meter replacement.
                    if row.start_date > previous.end_date + timedelta(days=1):
                        self.stdout.write(self.style.WARNING(
                            f"    GAP: previous ended {previous.end_date}; next starts {row.start_date}"
                        ))
                    elif row.start_date < previous.end_date:
                        self.stdout.write(self.style.WARNING(
                            f"    OVERLAP: previous ends {previous.end_date}; next starts {row.start_date}"
                        ))
                previous = row
            self.stdout.write("")

        invoice_numbers = options["invoice_numbers"]
        if invoice_numbers:
            self.stdout.write("INVOICE REFERENCES")
            invoices = (
                Invoice.objects.filter(invoice_number__in=invoice_numbers)
                .select_related("lease", "lease__tenant")
                .prefetch_related("items")
            )
            found = {invoice.invoice_number for invoice in invoices}
            for number in invoice_numbers:
                if number not in found:
                    self.stdout.write(self.style.WARNING(f"  {number}: not found"))
            for invoice in invoices:
                self.stdout.write(
                    f"  {invoice.invoice_number}: id={invoice.pk} lease={invoice.lease_id} "
                    f"status={invoice.status} issue_date={invoice.issue_date} amount={invoice.amount}"
                )
                for item in invoice.items.all():
                    description = (item.description or "").replace("\n", " ")
                    meter_text = description if "Meter#=" in description else "(no meter reference)"
                    self.stdout.write(f"    item={item.pk} amount={item.amount} {meter_text}")

        self.stdout.write(self.style.SUCCESS("Audit complete. READ ONLY; no changes were made."))
