from decimal import Decimal

from django.http import JsonResponse
from django.urls import reverse
from django_tables2.views import SingleTableView

from core.utils.date_filters import apply_date_range_filter
from invoices.models import SecurityDepositTransaction
from invoices.services import security_deposit_totals
from leases.models import Lease
from payments.ledger import CashLedgerRow
from payments.models import Payment
from payments.tables_cash_ledger import CashLedgerTable
from properties.models import Property, Unit
from tenants.models import Tenant
from utils.pdf_export import handle_export


def _lease_balance(lease):
    v = getattr(lease, "get_balance", 0)
    return v() if callable(v) else v


def _dec(v):
    return Decimal(v or 0)


class CashLedgerView(SingleTableView):
    table_class = CashLedgerTable
    template_name = "payments/cash_ledger.html"
    paginate_by = 20

    def get_queryset(self):
        return Payment.objects.none()

    def get_table_data(self):
        request = self.request

        # ---------- Lease base filters ----------
        leases = Lease.objects.select_related("tenant", "unit", "unit__property")

        property_id = request.GET.get("property")
        tenant_id = request.GET.get("tenant")
        unit_id = request.GET.get("unit")
        include_inactive = request.GET.get("include_inactive") == "on"

        if not include_inactive:
            leases = leases.filter(status="active")
        if property_id:
            leases = leases.filter(unit__property_id=property_id)
        if tenant_id:
            leases = leases.filter(tenant_id=tenant_id)
        if unit_id:
            leases = leases.filter(unit_id=unit_id)

        lease_ids = list(leases.values_list("id", flat=True))

        # ---------- Querysets ----------
        payments = (
            Payment.objects.filter(lease_id__in=lease_ids)
            .select_related("lease__tenant", "lease__unit", "lease__unit__property", "payment_method")
            .select_related("allocation")
        )

        sec_qs = (
            SecurityDepositTransaction.objects.filter(lease_id__in=lease_ids)
            .exclude(type="REQUIRED")
            .select_related("lease", "lease__tenant", "lease__unit", "lease__unit__property")
        )

        # ---------- Date filters ----------
        payments = apply_date_range_filter(
            payments,
            date_field="payment_date",
            date_range=request.GET.get("date_range"),
            start_date=request.GET.get("start_date"),
            end_date=request.GET.get("end_date"),
        )

        sec_qs = apply_date_range_filter(
            sec_qs,
            date_field="date",
            date_range=request.GET.get("date_range"),
            start_date=request.GET.get("start_date"),
            end_date=request.GET.get("end_date"),
        )

        # ✅ prevent double rows: split security transactions must not appear separately
        # (Your SecurityDepositTransaction has allocation FK/OneToOne, so this is correct.)
        sec_qs = sec_qs.filter(allocation__isnull=True)

        # ---------- Precompute security totals per lease ----------
        sec_totals_map = {l.id: security_deposit_totals(l) for l in leases}

        rows = []

        # ---------- Build Payment rows ----------
        # ---------- Build Payment rows ----------
        # ---------- Build Payment rows ----------
        # ---------- Build Payment rows ----------
        for p in payments:
            sec_totals = sec_totals_map.get(p.lease_id) or {"balance_to_collect": 0}
            alloc = getattr(p, "allocation", None)

            # Defaults = Payment routes (safe even if allocation missing)
            view_url = reverse("payments:payment_detail", args=[p.id])
            edit_url = reverse("payments:payment_update", args=[p.id])
            delete_url = reverse("payments:payment_delete", args=[p.id])
            wa_url = reverse("payments:api_payment_receipt_whatsapp", args=[p.id])

            allocation_id = None
            is_split = False
            lease_amt = Decimal("0.00")
            sec_amt = Decimal("0.00")

            description = (p.description or p.notes or "").strip()

            if alloc:
                allocation_id = alloc.id
                lease_amt = Decimal(getattr(alloc, "lease_amount", 0) or 0)
                sec_amt = Decimal(getattr(alloc, "security_amount", 0) or 0)
                is_split = (sec_amt != Decimal("0.00"))  # treat true split only when sec portion exists

                # Allocation routes override
                view_url = reverse("payments:allocation_detail", args=[allocation_id])
                edit_url = reverse("payments:allocation_edit", args=[allocation_id])      # IMPORTANT: use allocation_edit (see section 3)
                delete_url = reverse("payments:allocation_delete", args=[allocation_id])
                wa_url = reverse("payments:api_allocation_receipt_whatsapp", args=[allocation_id])

            rows.append(CashLedgerRow(
                source="PAYMENT",
                source_type="PAYMENT",
                source_id=p.id,
                lease=p.lease,
                date=p.payment_date,
                description=description,
                amount=Decimal(p.amount or 0),
                method=str(p.payment_method) if p.payment_method else "N/A",
                lease_balance=Decimal(_lease_balance(p.lease) or 0),
                security_balance=Decimal(sec_totals.get("balance_to_collect") or 0),

                view_url=view_url,
                edit_url=edit_url,
                delete_url=delete_url,
                wa_url=wa_url,

                allocation_id=allocation_id,
                is_split=is_split,
                lease_amount=lease_amt,
                security_amount=sec_amt,
            ))

        # ---------- Build standalone Security rows ----------
        for tx in sec_qs:
            amt = _dec(tx.amount)
            if tx.type in ("REFUND", "DAMAGE"):
                amt = -amt

            sec_totals = sec_totals_map.get(tx.lease_id) or {"balance_to_collect": 0}


            sec_edit_url = None
            try:
                sec_edit_url = reverse("leases:lease_security_edit", args=[tx.lease_id, tx.id])
            except Exception:
                sec_edit_url = None

            rows.append(
                CashLedgerRow(
                    source="SECURITY",
                    source_type=tx.type,
                    source_id=tx.id,
                    lease=tx.lease,
                    date=tx.date,
                    amount=amt,
                    method="Security Deposit",
                    description = (getattr(tx, "description", "") or getattr(tx, "notes", "") or "").strip(),
                    lease_balance=_dec(_lease_balance(tx.lease)),
                    security_balance=_dec(sec_totals.get("balance_to_collect")),
                    view_url=reverse("leases:lease_security_list", args=[tx.lease_id]),
                    edit_url=sec_edit_url,
                    delete_url=reverse("leases:lease_security_delete", args=[tx.lease_id, tx.id]),
                    wa_url=reverse("invoices:api_security_receipt_whatsapp", args=[tx.id]),
                )
            )

        rows.sort(key=lambda r: (r.date, r.source_id), reverse=True)
        return rows

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        ctx["all_properties"] = Property.objects.all()
        ctx["tenant_list"] = Tenant.objects.all().order_by("first_name", "last_name")

        property_id = self.request.GET.get("property")
        ctx["filtered_units"] = Unit.objects.filter(property_id=property_id) if property_id else Unit.objects.none()

        ctx["current_property"] = self.request.GET.get("property", "")
        ctx["current_unit"] = self.request.GET.get("unit", "")
        ctx["current_tenant"] = self.request.GET.get("tenant", "")
        ctx["include_inactive"] = self.request.GET.get("include_inactive", "") == "on"

        rows = self.get_table_data()
        ctx["total_amount"] = sum((r.amount or 0) for r in rows)
        ctx["total_label"] = "Cash Movements:"
        ctx["export_formats"] = self.table_class.Meta.export_formats
        return ctx

    def get_table(self, **kwargs):
        table = super().get_table(**kwargs)
        table.request = self.request
        return table

    def get(self, request, *args, **kwargs):
        if request.GET.get("ajax") == "1":
            rows = self.get_table_data()
            total = sum((r.amount or 0) for r in rows)
            return JsonResponse({"total_amount": float(total)})

        # --- FIX invalid page: redirect to last available page ---
        rows = self.get_table_data()
        page = request.GET.get("page")
        if page:
            try:
                paginator = Paginator(rows, self.paginate_by)
                paginator.page(page)  # validate
            except EmptyPage:
                url = request.build_absolute_uri()
                u = URL(url)
                u = u.update_query(page=str(paginator.num_pages or 1))
                return redirect(str(u))
            except Exception:
                pass

        self.object_list = self.get_queryset()
        table = self.get_table()

        start = request.GET.get("start_date")
        end = request.GET.get("end_date")
        title = f"Cash Ledger from {start} to {end}" if (start and end) else "Cash Ledger"

        export_response = handle_export(request, table, export_name="cash_ledger", title=title)
        if export_response:
            return export_response

        return super().get(request, *args, **kwargs)
