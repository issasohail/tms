from decimal import Decimal

from django.core.paginator import EmptyPage, Paginator
from django.db.models import Case, DecimalField, F, Sum, When
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django_tables2.views import SingleTableView

from core.utils.date_filters import apply_date_range_filter
from invoices.models import Invoice, SecurityDepositTransaction
from leases.models import Lease
from payments.payment_list_row import PaymentListRow
from payments.models import Payment
from payments.tables_payment_list import PaymentListTable
from properties.models import Property, Unit
from tenants.models import Tenant
from utils.pdf_export import handle_export


def _lease_balance(lease):
    v = getattr(lease, "get_balance", 0)
    return v() if callable(v) else v


def _dec(v):
    return Decimal(v or 0)


def _bulk_lease_balances(lease_ids):
    if not lease_ids:
        return {}

    invoice_totals = {
        row["lease_id"]: _dec(row["total"])
        for row in (
            Invoice.objects.filter(lease_id__in=lease_ids)
            .values("lease_id")
            .annotate(total=Sum("amount"))
        )
    }

    payment_totals = {
        row["lease_id"]: _dec(row["total"])
        for row in (
            Payment.objects.filter(lease_id__in=lease_ids)
            .values("lease_id")
            .annotate(
                total=Sum(
                    Case(
                        When(detail__isnull=False, then=F("detail__lease_amount")),
                        default=F("amount"),
                        output_field=DecimalField(max_digits=12, decimal_places=2),
                    )
                )
            )
        )
    }

    return {
        lease_id: invoice_totals.get(lease_id, Decimal("0.00"))
        - payment_totals.get(lease_id, Decimal("0.00"))
        for lease_id in lease_ids
    }


class PaymentListView(SingleTableView):
    table_class = PaymentListTable
    template_name = "payments/payment_list.html"
    paginate_by = 20

    def get_queryset(self):
        return Payment.objects.none()

    def get_table_data(self):
        if hasattr(self, "_payment_list_rows"):
            return self._payment_list_rows

        request = self.request

        # ---------- Lease base filters ----------
        leases = (
            Lease.objects
            .select_related("tenant", "unit", "unit__property")
            .only(
                "id",
                "tenant_id",
                "unit_id",
                "security_deposit",
                "status",
                "start_date",
                "tenant__id",
                "tenant__first_name",
                "tenant__last_name",
                "unit__id",
                "unit__property_id",
                "unit__unit_number",
                "unit__property__id",
                "unit__property__property_name",
            )
        )

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

        leases_list = list(leases)
        lease_ids = [lease.id for lease in leases_list]
        lease_map = {lease.id: lease for lease in leases_list}
        lease_balance_map = _bulk_lease_balances(lease_ids)

        # ---------- Querysets ----------
        payments = (
            Payment.objects.filter(lease_id__in=lease_ids)
            .select_related("payment_method", "detail")
            .only(
                "id",
                "lease_id",
                "payment_date",
                "amount",
                "description",
                "notes",
                "payment_method_id",
                "payment_method__id",
                "payment_method__name",
                "detail__id",
                "detail__payment_id",
                "detail__lease_amount",
                "detail__security_amount",
                "detail__security_type",
            )
        )

        sec_qs = (
            SecurityDepositTransaction.objects.filter(lease_id__in=lease_ids)
            .exclude(type="REQUIRED")
            .only(
                "id",
                "lease_id",
                "payment_detail_id",
                "date",
                "type",
                "amount",
                "notes",
            )
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
        sec_qs = sec_qs.filter(payment_detail__isnull=True)

        # ---------- Precompute security totals per lease ----------
        sec_summary = (
            SecurityDepositTransaction.objects
            .filter(lease_id__in=lease_ids)
            .values("lease_id", "type")
            .annotate(total=Sum("amount"))
        )

        sec_by_lease = {}
        for row in sec_summary:
            lease_id = row["lease_id"]
            tx_type = row["type"]
            sec_by_lease.setdefault(lease_id, {})[tx_type] = row["total"] or Decimal("0.00")

        sec_totals_map = {}
        for lease in leases_list:
            tx = sec_by_lease.get(lease.id, {})

            required = Decimal(lease.security_deposit or 0)
            paid = Decimal(tx.get("PAYMENT", 0) or 0)
            adjusted = Decimal(tx.get("ADJUST", 0) or 0)
            refunded = Decimal(tx.get("REFUND", 0) or 0)
            damaged = Decimal(tx.get("DAMAGE", 0) or 0)

            balance_to_collect = required - paid - adjusted
            if balance_to_collect < 0:
                balance_to_collect = Decimal("0.00")

            sec_totals_map[lease.id] = {
                "balance_to_collect": balance_to_collect,
            }
            lease._cached_get_balance = lease_balance_map.get(lease.id, Decimal("0.00"))
            lease._cached_security_due = balance_to_collect

        rows = []

        # ---------- Build Payment rows ----------
        # ---------- Build Payment rows ----------
        # ---------- Build Payment rows ----------
        # ---------- Build Payment rows ----------
        for p in payments:
            sec_totals = sec_totals_map.get(p.lease_id) or {"balance_to_collect": 0}
            alloc = getattr(p, "detail", None)

            # Defaults = Payment routes (safe even if detail is missing)
            view_url = reverse("payments:payment_detail", args=[p.id])
            edit_url = reverse("payments:payment_update", args=[p.id])
            delete_url = reverse("payments:payment_delete", args=[p.id])
            wa_url = reverse("payments:api_payment_receipt_whatsapp", args=[p.id])

            payment_detail_id = None
            is_split = False
            lease_amt = Decimal("0.00")
            sec_amt = Decimal("0.00")

            description = (p.description or p.notes or "").strip()
            row_source_type = "PAYMENT"
            row_amount = Decimal(p.amount or 0)

            if alloc:
                payment_detail_id = alloc.id
                lease_amt = Decimal(getattr(alloc, "lease_amount", 0) or 0)
                sec_amt = Decimal(getattr(alloc, "security_amount", 0) or 0)
                sec_type = (getattr(alloc, "security_type", "") or "PAYMENT").upper()
                row_source_type = sec_type if sec_amt > 0 else "PAYMENT"
                is_split = (sec_amt != Decimal("0.00"))  # treat true split only when sec portion exists
                if sec_type == "REFUND":
                    row_amount = lease_amt - sec_amt
                elif lease_amt < 0:
                    row_source_type = "LEASE_REFUND"

                # Payment-facing routes use Payment.pk; PaymentDetail.pk is used for split actions.
                view_url = reverse("payments:payment_detail", args=[p.id])
                edit_url = reverse("payments:payment_update", args=[p.id])
                delete_url = reverse("payments:payment_delete", args=[p.id])
                wa_url = reverse("payments:api_payment_detail_receipt_whatsapp", args=[payment_detail_id])

            rows.append(PaymentListRow(
                source="PAYMENT",
                source_type=row_source_type,
                source_id=p.id,
                lease=lease_map[p.lease_id],
                date=p.payment_date,
                description=description,
                amount=row_amount,
                method=str(p.payment_method) if p.payment_method else "N/A",
                lease_balance=lease_balance_map.get(p.lease_id, Decimal("0.00")),
                security_balance=Decimal(sec_totals.get("balance_to_collect") or 0),

                view_url=view_url,
                edit_url=edit_url,
                delete_url=delete_url,
                wa_url=wa_url,

                payment_detail_id=payment_detail_id,
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
                PaymentListRow(
                    source="SECURITY",
                    source_type=tx.type,
                    source_id=tx.id,
                    lease=lease_map[tx.lease_id],
                    date=tx.date,
                    amount=amt,
                    method="Security Deposit",
                    description = (getattr(tx, "description", "") or getattr(tx, "notes", "") or "").strip(),
                    lease_balance=lease_balance_map.get(tx.lease_id, Decimal("0.00")),
                    security_balance=_dec(sec_totals.get("balance_to_collect")),
                    view_url=reverse("leases:lease_security_list", args=[tx.lease_id]),
                    edit_url=sec_edit_url,
                    delete_url=reverse("leases:lease_security_delete", args=[tx.lease_id, tx.id]),
                    wa_url=reverse("invoices:api_security_receipt_whatsapp", args=[tx.id]),
                )
            )

        rows.sort(key=lambda r: (r.date, r.source_id), reverse=True)
        self._payment_list_rows = rows
        return rows

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        ctx["all_properties"] = Property.objects.only("id", "property_name")
        ctx["tenant_list"] = Tenant.objects.only("id", "first_name", "last_name").order_by("first_name", "last_name")

        property_id = self.request.GET.get("property")
        ctx["filtered_units"] = (
            Unit.objects.only("id", "property_id", "unit_number").filter(property_id=property_id)
            if property_id
            else Unit.objects.none()
        )

        ctx["current_property"] = self.request.GET.get("property", "")
        ctx["current_unit"] = self.request.GET.get("unit", "")
        ctx["current_tenant"] = self.request.GET.get("tenant", "")
        ctx["include_inactive"] = self.request.GET.get("include_inactive", "") == "on"

        rows = self.get_table_data()
        ctx["total_amount"] = sum((r.amount or 0) for r in rows)
        ctx["total_label"] = "Payment Total:"
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
                query = request.GET.copy()
                query["page"] = str(paginator.num_pages or 1)
                return redirect(f"{request.path}?{query.urlencode()}")
            except Exception:
                pass

        self.object_list = self.get_queryset()
        table = self.get_table()

        start = request.GET.get("start_date")
        end = request.GET.get("end_date")
        title = f"Payment List from {start} to {end}" if (start and end) else "Payment List"

        export_response = handle_export(request, table, export_name="payment_list", title=title)
        if export_response:
            return export_response

        return super().get(request, *args, **kwargs)
