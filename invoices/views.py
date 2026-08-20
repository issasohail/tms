from accounts.access import restrict_queryset_to_properties
# invoices/views.py
# adjust import if Category lives elsewhere
import asyncio
import json
import logging
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from urllib.parse import urlsplit
from django.conf import settings
from django.apps import apps  # (ensure this import exists at the top)
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import EmailMessage
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import (
    Case,
    Count,
    DecimalField,
    F,
    Prefetch,
    ProtectedError,
    Q,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.forms import inlineformset_factory
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    JsonResponse,
    StreamingHttpResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import (
    reverse,
    reverse_lazy,
)
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.timezone import now
from django.views import View
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
)
from django_tables2 import SingleTableView
from weasyprint import HTML

from core.models import GlobalSettings
from core.public_urls import build_public_path_url, build_public_url
from leases.models import Lease
from payments.models import Payment
from properties.models import Property, Unit  # adjust imports

from .forms import InvoiceForm, InvoiceItemForm

# top of views.py
from .models import (
    Invoice,  # and InvoiceItem if separate  # adjust if Category is elsewhere
    InvoiceItem,
    InvoiceStatusHistory,
    ItemCategory,
    RecurringCharge,  # IMPORTANT: we need the model here
    SecurityDepositTransaction,
    WaterBill,
)
from .public_links import load_public_invoice_token, make_public_invoice_token
from .services import (
    active_leases_qs,
    ensure_month_invoice,
    first_of_month,
    invoice_due_date_from_lease,
    run_monthly_billing_for,
    security_deposit_totals,
)
from .tables import InvoiceTable

logger = logging.getLogger(__name__)
# at top if not present
ITEMS_PREFIX = "items"
CATEGORY_CACHE_KEY = "invoices.active_item_categories"

InvoiceItemFormset = inlineformset_factory(
    Invoice,
    InvoiceItem,
    form=InvoiceItemForm,  # <- use the form that includes amount
    extra=0,
    can_delete=True,
    validate_min=True,
    min_num=1,
)

# invoices/views.py  (only the relevant parts)


class InvoiceListView(SingleTableView):
    model = Invoice
    table_class = InvoiceTable
    template_name = "invoices/invoice_list.html"
    paginate_by = 45

    def _period_to_dates(self, period: str):
        if not period:
            return None, None
        today = timezone.localdate()
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        if period == "today":
            return today, today
        if period == "yesterday":
            y = today - timedelta(days=1)
            return y, y
        if period == "this_week":
            return monday, sunday
        if period == "last_week":
            lm = monday - timedelta(days=7)
            return lm, lm + timedelta(days=6)
        if period == "this_month":
            start = today.replace(day=1)
            end = (
                (start.replace(month=start.month + 1, day=1) - timedelta(days=1))
                if start.month < 12
                else (
                    start.replace(year=start.year + 1, month=1, day=1)
                    - timedelta(days=1)
                )
            )
            return start, end
        if period == "last_month":
            this_start = today.replace(day=1)
            last_end = this_start - timedelta(days=1)
            last_start = last_end.replace(day=1)
            return last_start, last_end
        if period == "this_year":
            return date(today.year, 1, 1), date(today.year, 12, 31)
        return None, None

    def get_queryset(self):
        qs = (
            Invoice.objects.select_related(
                "lease", "lease__tenant", "lease__unit", "lease__unit__property"
            )
            .prefetch_related(
                Prefetch(
                    "items",
                    queryset=InvoiceItem.objects.select_related("category").only(
                        "id",
                        "invoice_id",
                        "category_id",
                        "description",
                        "amount",
                        "category__id",
                        "category__name",
                    ),
                )
            )
            .only(
                "id",
                "lease_id",
                "invoice_number",
                "issue_date",
                "due_date",
                "amount",
                "status",
                "lifecycle_status",
                "lifecycle_status_reason",
                "description",
                "lease__id",
                "lease__tenant_id",
                "lease__unit_id",
                "lease__tenant__id",
                "lease__tenant__first_name",
                "lease__tenant__last_name",
                "lease__tenant__phone",
                "lease__unit__id",
                "lease__unit__property_id",
                "lease__unit__unit_number",
                "lease__unit__property__id",
                "lease__unit__property__property_name",
                "lease__security_deposit",
            )
        )

        qs = restrict_queryset_to_properties(qs, self.request.user, "lease__unit__property")

        r = self.request
        prop = r.GET.get("property") or r.GET.get("property_id")
        unit = r.GET.get("unit") or r.GET.get("unit_id")
        tenant = r.GET.get("tenant") or r.GET.get("tenant_id")
        lease = r.GET.get("lease") or r.GET.get("lease_id")  # <= IMPORTANT
        start = r.GET.get("start_date")
        end = r.GET.get("end_date")
        period = r.GET.get("period")
        status_filter = (r.GET.get("status") or "").strip()

        if period and not (start or end):
            s, e = self._period_to_dates(period)
            if s:
                start = s.isoformat()
            if e:
                end = e.isoformat()

        if prop:
            qs = qs.filter(lease__unit__property_id=prop)
        if unit:
            qs = qs.filter(lease__unit_id=unit)
        if tenant:
            qs = qs.filter(lease__tenant_id=tenant)
        if lease:
            qs = qs.filter(lease_id=lease)  # <= IMPORTANT
        if start:
            qs = qs.filter(issue_date__gte=start)
        if end:
            qs = qs.filter(issue_date__lte=end)
        lifecycle_values = {choice[0] for choice in Invoice.LIFECYCLE_STATUS_CHOICES}
        if status_filter in lifecycle_values:
            qs = qs.filter(lifecycle_status=status_filter)
        elif status_filter == "paid":
            qs = qs.filter(status="paid")
        elif status_filter == "partially_paid":
            qs = qs.filter(status="partially_paid")
        elif status_filter == "overdue":
            qs = qs.filter(Q(status="overdue") | (~Q(status__in=["paid", "cancelled"]) & Q(due_date__lt=timezone.localdate())))
        elif status_filter == "unpaid":
            qs = qs.exclude(status__in=["paid", "partially_paid", "cancelled", "overdue"]).filter(due_date__gte=timezone.localdate())
        elif status_filter == "overpaid":
            qs = qs.filter(status="overpaid")

        return qs  # don't force order here; table default covers first load

    def _attach_page_lease_balances(self, table):
        """
        The invoice list only needs lease balance for the WhatsApp button rows.
        Computing it in the main queryset adds correlated subqueries to every
        invoice before pagination, so calculate it for the displayed page only.
        """
        records = []
        try:
            records = [row.record for row in table.paginated_rows]
        except Exception:
            page = getattr(table, "page", None)
            if page is not None:
                records = list(getattr(page, "object_list", []) or [])

        lease_ids = sorted({record.lease_id for record in records if record.lease_id})
        if not lease_ids:
            return

        money_field = DecimalField(max_digits=12, decimal_places=2)
        payment_totals = {
            row["lease_id"]: row["total"] or Decimal("0.00")
            for row in (
                Payment.objects.filter(lease_id__in=lease_ids)
                .values("lease_id")
                .annotate(
                    total=Coalesce(
                        Sum(
                            Case(
                                When(
                                    detail__isnull=False,
                                    then=F("detail__lease_amount"),
                                ),
                                default=F("amount"),
                                output_field=money_field,
                            )
                        ),
                        Value(Decimal("0.00")),
                        output_field=money_field,
                    )
                )
            )
        }
        security_rows = (
            SecurityDepositTransaction.objects.filter(lease_id__in=lease_ids)
            .values("lease_id", "type")
            .annotate(
                total=Coalesce(
                    Sum("amount"), Value(Decimal("0.00")), output_field=money_field
                )
            )
        )
        security_totals = {}
        for row in security_rows:
            lease_id = row["lease_id"]
            security_totals.setdefault(
                lease_id, {"PAYMENT": Decimal("0.00"), "ADJUST": Decimal("0.00")}
            )
            security_totals[lease_id][row["type"]] = row["total"] or Decimal("0.00")

        allocation_by_invoice = {}
        invoice_rows = (
            Invoice.objects.filter(lease_id__in=lease_ids)
            .order_by("lease_id", "issue_date", "id")
            .values(
                "id", "lease_id", "amount", "due_date",
                "lifecycle_status", "status",
            )
        )
        available_by_lease = dict(payment_totals)
        today = timezone.localdate()
        last_invoice_id_by_lease = {}
        rows_buffer = list(invoice_rows)
        invoice_totals = defaultdict(lambda: Decimal("0.00"))
        eligible_rows = []
        for row in rows_buffer:
            invoice_totals[row["lease_id"]] += row["amount"] or Decimal("0.00")
            if (
                row["lifecycle_status"] in {"cancelled", "void"}
                or row["status"] == "cancelled"
            ):
                continue
            eligible_rows.append(row)
            last_invoice_id_by_lease[row["lease_id"]] = row["id"]
        for row in eligible_rows:
            lease_id = row["lease_id"]
            amount = row["amount"] or Decimal("0.00")
            available = available_by_lease.get(lease_id, Decimal("0.00"))
            allocated = min(max(available, Decimal("0.00")), amount)
            available -= allocated
            available_by_lease[lease_id] = available
            outstanding = max(amount - allocated, Decimal("0.00"))
            if amount <= 0 or allocated >= amount:
                payment_status = (
                    "overpaid"
                    if row["id"] == last_invoice_id_by_lease.get(lease_id) and available > 0
                    else "paid"
                )
            elif allocated > 0:
                payment_status = "partially_paid"
            elif row["due_date"] and row["due_date"] < today:
                payment_status = "overdue"
            else:
                payment_status = "unpaid"
            allocation_by_invoice[row["id"]] = (allocated, outstanding, payment_status)

        for record in records:
            allocation = allocation_by_invoice.get(
                record.pk, (Decimal("0.00"), record.amount or Decimal("0.00"), "unpaid")
            )
            record._accounting_allocation_cache = allocation
            record.dashboard_amount_paid = allocation[0]
            record.dashboard_invoice_outstanding = allocation[1]
            record.dashboard_payment_status = allocation[2]
            balance = invoice_totals.get(
                record.lease_id, Decimal("0.00")
            ) - payment_totals.get(record.lease_id, Decimal("0.00"))
            security_required = getattr(
                getattr(record, "lease", None), "security_deposit", None
            ) or Decimal("0.00")
            security_paid = security_totals.get(record.lease_id, {}).get(
                "PAYMENT", Decimal("0.00")
            )
            security_balance = max(security_required - security_paid, Decimal("0.00"))
            total_balance = balance + security_balance
            record.dashboard_lease_balance = balance
            record.dashboard_security_balance = security_balance
            record.dashboard_total_balance = total_balance
            if getattr(record, "lease", None) is not None:
                record.lease._cached_get_balance = balance
                record.lease._cached_security_due = security_balance

    def get_table(self, **kwargs):
        table = super().get_table(**kwargs)
        # Only set default when there's no user sort in querystring
        sort_param = table.prefixed_order_by_field  # e.g. 'sort'
        if not self.request.GET.get(sort_param):
            table.order_by = ("invoice_number",)
        self._attach_page_lease_balances(table)
        return table

    def get_context_data(self, **kwargs):
        Property = apps.get_model("properties", "Property")
        Lease = apps.get_model("leases", "Lease")
        Unit = apps.get_model("properties", "Unit")

        ctx = {"view": self}
        table = self.get_table(**self.get_table_kwargs())
        ctx[self.get_context_table_name(table)] = table
        today = timezone.localdate()

        # Properties
        props = Property.objects.only("id", "property_name").order_by("property_name")
        ctx["property_options"] = [{"id": p.id, "name": p.property_name} for p in props]

        # Lease options (independent of property/unit), sorted by tenant first name
        show_inactive = bool(self.request.GET.get("show_inactive"))
        ctx["show_inactive"] = show_inactive

        leases_qs = Lease.objects.select_related(
            "tenant", "unit", "unit__property"
        ).only(
            "id",
            "tenant_id",
            "unit_id",
            "start_date",
            "end_date",
            "status",
            "tenant__id",
            "tenant__first_name",
            "tenant__last_name",
            "unit__id",
            "unit__property_id",
            "unit__unit_number",
            "unit__property__id",
            "unit__property__property_name",
        )
        if not show_inactive:
            active_filter = {"status": "active"} if hasattr(Lease, "status") else {}
            leases_qs = leases_qs.filter(**active_filter)
            if hasattr(Lease, "end_date"):
                leases_qs = leases_qs.filter(end_date__isnull=True) | leases_qs.filter(
                    end_date__gte=today
                )

        leases_list = list(leases_qs.order_by("-start_date"))

        lease_options = []
        for L in leases_list:
            t = getattr(L, "tenant", None)
            u = getattr(L, "unit", None)
            p = getattr(u, "property", None) if u else None
            first = (getattr(t, "first_name", "") or "").strip()
            last = (getattr(t, "last_name", "") or "").strip()
            tname = (f"{first}".strip()) or (getattr(t, "name", None) or "Tenant")
            # <= limit tenant name in the Lease filter to 15 chars
            short_tname = tname[:15]
            unit_no = getattr(u, "unit_number", "") if u else ""
            prop_nm = getattr(p, "property_name", "") if p else ""
            end = getattr(L, "end_date", None)
            end_txt = end.strftime("%b %d,%Y") if end else "—"
            is_active = (getattr(L, "status", "active") == "active") and (
                not end or end >= today
            )
            status_txt = "Active" if is_active else "Expired"
            lease_options.append(
                {
                    "id": L.id,
                    "label": f"{tname} — {prop_nm}-{unit_no} — {end_txt} — {status_txt}",
                    "sort_key": first.lower() if first else tname.lower(),
                }
            )
        lease_options.sort(key=lambda x: x["sort_key"])
        ctx["lease_options"] = lease_options

        # Units by property (Unit — Tenant — End — Status)
        latest_by_unit = {}
        for L in leases_list:
            uid = getattr(L, "unit_id", None)
            if uid is None:
                continue
            prev = latest_by_unit.get(uid)
            cur_end = getattr(L, "end_date", None)
            prev_end = getattr(prev, "end_date", None) if prev else None
            if not prev or (cur_end and (not prev_end or cur_end > prev_end)):
                latest_by_unit[uid] = L

        by_prop = {}
        units = Unit.objects.only("id", "property_id", "unit_number").all()
        for u in units:
            L = latest_by_unit.get(u.id)
            tname, end_txt, is_active = "", "—", False
            if L:
                t = getattr(L, "tenant", None)
                first = (getattr(t, "first_name", "") or "").strip()
                last = (getattr(t, "last_name", "") or "").strip()
                short_tname = tname[:15]
                tname = (f"{first}".strip()) or (getattr(t, "name", None) or "")
                end = getattr(L, "end_date", None)
                if end:
                    end_txt = end.strftime("%b %d,%Y")
                is_active = (getattr(L, "status", "active") == "active") and (
                    not end or end >= today
                )
            status_txt = "Active" if is_active else "Expired"
            by_prop.setdefault(u.property_id, []).append(
                {
                    "id": u.id,
                    "label": f"{u.unit_number} — {tname or 'Vacant'} — {end_txt} — {status_txt}",
                }
            )
        ctx["units_by_property_json"] = json.dumps(by_prop)
        ctx["invoice_status_filter_options"] = [
            ("draft", "Draft"),
            ("issued", "Issued"),
            ("unpaid", "Unpaid"),
            ("partially_paid", "Partially Paid"),
            ("paid", "Paid"),
            ("overpaid", "Overpaid"),
            ("overdue", "Overdue"),
            ("disputed", "Disputed"),
            ("cancelled", "Cancelled"),
            ("void", "Void"),
            ("written_off", "Written Off"),
        ]
        return ctx


class InvoiceCreateView(LoginRequiredMixin, CreateView):
    model = Invoice
    form_class = InvoiceForm
    template_name = "invoices/invoice_form.html"
    success_url = reverse_lazy("invoices:invoice_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # ✅ bind with the same prefix your template/JS expects
        ctx["items"] = InvoiceItemFormset(
            self.request.POST or None, prefix=ITEMS_PREFIX
        )
        # add active categories for datalist suggestions
        ctx["categories"] = (
            ItemCategory.objects.filter(is_active=True)
            .order_by("name")
            .values("id", "name")
        )
        return ctx

    def form_valid(self, form):
        ctx = self.get_context_data()
        items = ctx["items"]

        # (optional) log details to your console
        logger.error(
            "Item errors=%s non_form=%s", items.errors, items.non_form_errors()
        )
        # Log useful context
        lease_id = form.cleaned_data.get("lease") and form.cleaned_data["lease"].id
        logger.info(
            "InvoiceCreateView.form_valid: lease_id=%s POST_items_total=%s",
            lease_id,
            self.request.POST.get("items-TOTAL_FORMS"),
        )

        if not items.is_valid():
            logger.error("Items formset invalid: %s", items.errors)
            messages.error(
                self.request, "Item rows have errors. Please fix and save again."
            )
            return self.render_to_response(self.get_context_data(form=form))

        # Hard guard: ensure amount is present before hitting the DB
        for f in items.forms:
            if f.cleaned_data.get("DELETE"):
                continue
            if f.cleaned_data.get("amount") in (None, ""):
                f.add_error("amount", "Amount is required.")
                messages.error(
                    self.request, "Item rows have errors. Please fix and save again."
                )
                return self.render_to_response(self.get_context_data(form=form))

        lease = form.cleaned_data.get("lease")
        issue_date = form.cleaned_data.get("issue_date") or timezone.localdate()
        form.instance.due_date = invoice_due_date_from_lease(
            lease,
            issue_date,
            fallback=form.cleaned_data.get("due_date") or issue_date,
        )

        try:
            with transaction.atomic():
                response = super().form_valid(form)  # saves self.object
                items.instance = self.object
                items.save()

                # Expand invoice.description from items (Category: description, ...)
                parts = []
                for it in self.object.items.select_related("category"):
                    nm = it.category.name if it.category_id else ""
                    ds = (it.description or "").strip()
                    if nm and ds:
                        parts.append(f"{nm}: {ds}")
                    elif nm:
                        parts.append(nm)
                    elif ds:
                        parts.append(ds)
                self.object.description = ", ".join(parts)
                self.object.save(update_fields=["description"])

                logger.info(
                    "Invoice saved OK: id=%s total_items=%s",
                    self.object.pk,
                    self.object.items.count(),
                )
                return response
        except Exception as e:
            logger.exception("Invoice save failed: %s", e)
            messages.error(self.request, f"Could not save invoice: {e}")
            return self.render_to_response(self.get_context_data(form=form))

    def form_invalid(self, form):
        logger.error("InvoiceCreateView.form_invalid errors=%s", form.errors)
        messages.error(self.request, "Form has errors. Please review and try again.")
        return super().form_invalid(form)

    def get_success_url(self):
        return reverse("invoices:invoice_detail", kwargs={"pk": self.object.pk})

    def get_initial(self):
        initial = super().get_initial()
        today = timezone.localdate()
        lease = None
        lease_id = self.request.GET.get("lease") or self.request.GET.get("lease_id")
        if lease_id:
            lease = Lease.objects.filter(pk=lease_id).first()
        initial.setdefault("issue_date", today)
        initial.setdefault(
            "due_date", invoice_due_date_from_lease(lease, today, fallback=today)
        )
        return initial


class InvoiceUpdateView(LoginRequiredMixin, UpdateView):
    model = Invoice
    form_class = InvoiceForm
    template_name = "invoices/invoice_form.html"
    success_url = reverse_lazy("invoices:invoice_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # ✅ keep the same prefix on update and bind the instance
        if self.request.method in ("POST", "PUT"):
            ctx["items"] = InvoiceItemFormset(
                self.request.POST, instance=self.object, prefix=ITEMS_PREFIX
            )
        else:
            ctx["items"] = InvoiceItemFormset(instance=self.object, prefix=ITEMS_PREFIX)
        ctx["categories"] = (
            ItemCategory.objects.filter(is_active=True)
            .order_by("name")
            .values("id", "name")
        )
        return ctx

    def form_valid(self, form):
        ctx = self.get_context_data()
        items = ctx["items"]
        if not items.is_valid():
            logger.error(
                "Item errors=%s non_form=%s", items.errors, items.non_form_errors()
            )
            messages.error(
                self.request, "Item rows have errors. Please fix and save again."
            )
            return self.render_to_response(self.get_context_data(form=form))

        with transaction.atomic():
            response = super().form_valid(form)
            items.instance = self.object
            items.save()

        return response

    def get_success_url(self):
        url = reverse("invoices:invoice_detail", kwargs={"pk": self.object.pk})
        settlement = {
            key: self.request.GET.get(key) or self.request.POST.get(key)
            for key in ("settlement", "lease_id", "end_date")
        }
        settlement = {key: value for key, value in settlement.items() if value}
        return f"{url}?{urlencode(settlement)}" if settlement else url


# invoices/views.py

# invoices/views.py


class InvoiceDetailView(DetailView):
    model = Invoice
    template_name = "invoices/invoice_detail.html"

    def get_queryset(self):
        # PERF: avoids N+1 on invoice->lease->tenant/unit/property and invoice items/categories.
        return (
            super()
            .get_queryset()
            .select_related(
                "lease__tenant",
                "lease__unit__property",
            )
            .prefetch_related(
                Prefetch(
                    "items", queryset=InvoiceItem.objects.select_related("category")
                ),
                "lease__invoices",
                "late_fee_reminders",
                Prefetch(
                    "status_history",
                    queryset=InvoiceStatusHistory.objects.select_related("changed_by"),
                ),
                Prefetch(
                    "lease__payments",
                    queryset=Payment.objects.select_related("detail"),
                ),
                "lease__security_transactions",
            )
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        inv = self.object

        # Preload items + category
        items = list(inv.items.all())

        # Combined description = "Category: desc, Category: desc, ..."
        parts = []
        for it in items:
            if it.category and it.description:
                parts.append(f"{it.category.name}: {it.description}")
            elif it.category:
                parts.append(f"{it.category.name}")
            elif it.description:
                parts.append(it.description)
        ctx["combined_description"] = ", ".join(parts)

        # Compute total from items (don’t trust stale invoice.amount)
        ctx["computed_total"] = sum((item.amount for item in items), Decimal("0.00"))

        # For the category list box (datalist suggestions)
        categories = cache.get(CATEGORY_CACHE_KEY)
        if categories is None:
            categories = list(
                ItemCategory.objects.filter(is_active=True)
                .order_by("name")
                .values("id", "name")
            )
            cache.set(CATEGORY_CACHE_KEY, categories, 60)
        ctx["categories"] = categories

        # PERF: lease balance is expensive; cached Lease financial summary avoids repeated aggregates.
        ctx["lease_balance"] = (
            inv.lease.get_balance if inv.lease_id else Decimal("0.00")
        )
        amount_paid, invoice_outstanding, payment_status = inv.accounting_allocation()
        ctx["invoice_amount_paid"] = amount_paid
        ctx["invoice_outstanding"] = invoice_outstanding
        ctx["invoice_payment_status"] = payment_status
        ctx["invoice_payment_status_display"] = inv.payment_status_display
        if self.request.user.has_perm("invoices.view_invoice_status_history") or self.request.user.is_superuser:
            ctx["invoice_status_history"] = inv.status_history.all()
        else:
            ctx["invoice_status_history"] = []

        # 🔹 NEW: security deposit totals for this invoice's lease
        lease = getattr(inv, "lease", None)
        if lease:
            sec_totals = security_deposit_totals(lease)
        else:
            sec_totals = {
                "required": 0,
                "paid_in": 0,
                "refunded": 0,
                "damages": 0,
                "balance_to_collect": 0,
                "currently_held": 0,
            }

        ctx["sec_totals"] = sec_totals

        settlement_end_date = None
        if self.request.GET.get("settlement") == "1" and inv.lease_id:
            try:
                settlement_end_date = datetime.strptime(
                    self.request.GET.get("end_date", ""), "%Y-%m-%d"
                ).date()
            except ValueError:
                settlement_end_date = None
        ctx["settlement_mode"] = bool(
            settlement_end_date
            and str(inv.lease_id) == str(self.request.GET.get("lease_id") or inv.lease_id)
        )
        if ctx["settlement_mode"]:
            ctx["settlement_end_date"] = settlement_end_date
            ctx["settlement_query"] = urlencode(
                {
                    "settlement": "1",
                    "lease_id": inv.lease_id,
                    "end_date": settlement_end_date.isoformat(),
                }
            )
            ctx["settlement_return_url"] = (
                reverse("leases:lease_detail", args=[inv.lease_id])
                + "?"
                + urlencode(
                    {
                        "open_end_lease": "1",
                        "end_date": settlement_end_date.isoformat(),
                    }
                )
            )

        return ctx


@login_required
@require_POST
def update_invoice_lifecycle_status(request, pk):
    if not request.user.has_perm("invoices.change_invoice_lifecycle_status") and not request.user.is_superuser:
        raise PermissionDenied

    invoice = get_object_or_404(Invoice, pk=pk)
    new_status = (request.POST.get("lifecycle_status") or "").strip()
    valid_statuses = {choice[0] for choice in Invoice.LIFECYCLE_STATUS_CHOICES}
    if new_status not in valid_statuses:
        messages.error(request, "Invalid invoice lifecycle status.")
        return redirect("invoices:invoice_detail", pk=invoice.pk)

    permission_by_status = {
        "cancelled": "invoices.cancel_invoice",
        "void": "invoices.void_invoice",
        "written_off": "invoices.write_off_invoice",
    }
    required_permission = permission_by_status.get(new_status)
    if required_permission and not (request.user.has_perm(required_permission) or request.user.is_superuser):
        raise PermissionDenied

    reason = (request.POST.get("reason") or "").strip()
    if new_status in {"cancelled", "void", "disputed", "written_off"} and not reason:
        messages.error(request, "A reason is required for this status change.")
        return redirect("invoices:invoice_detail", pk=invoice.pk)

    previous = invoice.lifecycle_status
    if previous == new_status and invoice.lifecycle_status_reason == reason:
        messages.info(request, "Invoice lifecycle status is already set to that value.")
        return redirect("invoices:invoice_detail", pk=invoice.pk)

    forwarded = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",", 1)[0].strip()
    ip_address = forwarded or request.META.get("REMOTE_ADDR") or None
    with transaction.atomic():
        invoice.lifecycle_status = new_status
        invoice.lifecycle_status_reason = reason
        invoice.lifecycle_status_updated_by = request.user
        invoice.lifecycle_status_updated_at = timezone.now()
        invoice.save(update_fields=[
            "lifecycle_status", "lifecycle_status_reason",
            "lifecycle_status_updated_by", "lifecycle_status_updated_at", "updated_at",
        ])
        InvoiceStatusHistory.objects.create(
            invoice=invoice, previous_status=previous, new_status=new_status,
            changed_by=request.user, reason=reason, ip_address=ip_address,
        )
    messages.success(request, "Invoice lifecycle status updated.")
    return redirect("invoices:invoice_detail", pk=invoice.pk)


def public_invoice_detail(request, token):
    try:
        data = load_public_invoice_token(token)
        invoice_id = data.get("invoice_id")
    except signing.SignatureExpired:
        return render(request, "invoices/public_invoice_expired.html", status=410)
    except signing.BadSignature:
        raise Http404("Invalid invoice link.")

    invoice = get_object_or_404(
        Invoice.objects.select_related(
            "lease__tenant",
            "lease__unit__property",
        ).prefetch_related(
            Prefetch("items", queryset=InvoiceItem.objects.select_related("category")),
            "lease__invoices",
            Prefetch(
                "lease__payments", queryset=Payment.objects.select_related("detail")
            ),
            "lease__security_transactions",
        ),
        pk=invoice_id,
    )
    items = list(invoice.items.all())
    lease = getattr(invoice, "lease", None)
    if lease:
        sec_totals = security_deposit_totals(lease)
        lease_balance = lease.get_balance
    else:
        sec_totals = {
            "required": 0,
            "paid_in": 0,
            "refunded": 0,
            "damages": 0,
            "balance_to_collect": 0,
            "currently_held": 0,
        }
        lease_balance = Decimal("0.00")
    combined_description = ", ".join(
        part
        for item in items
        for part in [
            (
                f"{item.category.name}: {item.description}"
                if item.category and item.description
                else item.category.name
                if item.category
                else item.description or ""
            )
        ]
        if part
    )
    return render(
        request,
        "invoices/public_invoice_detail.html",
        {
            "invoice": invoice,
            "items": items,
            "combined_description": combined_description,
            "computed_total": sum((item.amount for item in items), Decimal("0.00")),
            "lease_balance": lease_balance,
            "sec_totals": sec_totals,
        },
    )


class InvoiceDeleteView(LoginRequiredMixin, DeleteView):
    model = Invoice
    template_name = "invoices/invoice_confirm_delete.html"
    success_url = reverse_lazy("invoices:invoice_list")

    def delete(self, request, *args, **kwargs):
        messages.success(self.request, "Invoice deleted successfully.")
        return super().delete(request, *args, **kwargs)

    def get_success_url(self):
        if self.request.GET.get("settlement") == "1":
            lease_id = self.request.GET.get("lease_id") or self.object.lease_id
            end_date = self.request.GET.get("end_date") or ""
            return reverse("leases:lease_detail", args=[lease_id]) + "?" + urlencode(
                {
                    "settlement_return": "1",
                    "open_end_lease": "1",
                    "end_date": end_date,
                }
            )
        return_to = (self.request.GET.get("return_to") or "").strip()
        invoice_list_path = reverse("invoices:invoice_list")
        if (
            return_to
            and url_has_allowed_host_and_scheme(
                return_to,
                allowed_hosts={self.request.get_host()},
                require_https=self.request.is_secure(),
            )
            and urlsplit(return_to).path == invoice_list_path
        ):
            return return_to
        return str(self.success_url)


@login_required
@require_POST
@transaction.atomic
def invoice_move_out_prorate(request, pk):
    invoice = get_object_or_404(
        Invoice.objects.select_related("lease", "lease__unit"), pk=pk
    )
    if invoice.status in {"paid", "cancelled"}:
        return JsonResponse(
            {"ok": False, "error": "Paid or cancelled invoices cannot be prorated."},
            status=400,
        )
    try:
        end_date = datetime.strptime(request.POST.get("end_date", ""), "%Y-%m-%d").date()
        from leases.utils.end_lease import prorate_invoice_for_move_out

        result = prorate_invoice_for_move_out(invoice, end_date)
    except (ValidationError, ValueError, ArithmeticError) as exc:
        error = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        return JsonResponse({"ok": False, "error": error}, status=400)
    return JsonResponse(
        {
            "ok": True,
            "invoice_total": str(result["invoice"].amount or Decimal("0.00")),
            "occupied_days": result["occupied_days"],
            "billable_days": result["billable_days"],
            "days_in_month": result["days_in_month"],
            "interval_days": result["interval_days"],
            "interval_label": result["interval_label"],
            "electric_amount": str(result["electric_amount"]),
        }
    )


@login_required
def generate_monthly_invoices(request):
    """
    Backward-compatible endpoint: generate next month’s invoices (and recurring).
    """
    today = date.today()
    y = today.year + (1 if today.month == 12 else 0)
    m = 1 if today.month == 12 else (today.month + 1)
    period = date(y, m, 1)

    # IMPORTANT: fix monthly_rent usage (it's a Lease field, not Unit)
    # We centralize generation in the service now:
    run_monthly_billing_for(period)

    messages.success(request, f"Invoices generated for {period:%B %Y}.")
    # adjust to your invoice list name
    return redirect("invoices:invoice_list")


def render_to_pdf(template_name, context):
    """Generate PDF from HTML template"""
    try:
        html_string = render_to_string(template_name, context)
        html = HTML(string=html_string)
        pdf_content = html.write_pdf()
        if not pdf_content:
            raise Exception("PDF generation returned empty content")
        return pdf_content
    except Exception as e:
        raise Exception(f"PDF generation failed: {str(e)}")


def _invoice_pdf_context(invoice):
    items = list(invoice.items.select_related("category").all())
    parts = []
    for item in items:
        category_name = item.category.name if item.category_id else ""
        description = item.description or ""
        if category_name and description:
            parts.append(f"{category_name}: {description}")
        elif category_name:
            parts.append(category_name)
        elif description:
            parts.append(description)

    return {
        "invoice": invoice,
        "items": items,
        "computed_total": sum((item.amount for item in items), Decimal("0.00")),
        "combined_description": ", ".join(parts),
        "date": now().date(),
    }


@login_required
def send_invoice_email(request, invoice_id):
    try:
        invoice = get_object_or_404(
            Invoice.objects.select_related("lease__tenant", "lease__unit__property"),
            pk=invoice_id,
        )

        # Validate recipient email
        try:
            validate_email(invoice.lease.tenant.email)
        except ValidationError:
            messages.error(request, "Invalid recipient email address.")
            return redirect("invoices:invoice_detail", pk=invoice_id)

        context = _invoice_pdf_context(invoice)

        try:
            pdf_content = render_to_pdf("invoices/invoice_pdf.html", context)
        except Exception as e:
            messages.error(request, f"PDF generation failed: {str(e)}")
            return redirect("invoices:invoice_detail", pk=invoice_id)

        email = EmailMessage(
            subject=f"Invoice #{invoice.id}",
            body="Please find attached your invoice.",
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[invoice.lease.tenant.email],  # ✅ use lease.tenant
        )

        email.attach(f"invoice_{invoice.id}.pdf", pdf_content, "application/pdf")
        email.send()

        messages.success(request, "Invoice email sent successfully!")
        return redirect("invoices:invoice_detail", pk=invoice_id)

    except Exception as e:
        messages.error(request, f"Failed to send email: {str(e)}")
        return redirect("invoices:invoice_detail", pk=invoice_id)


class InvoicePDFView(View):
    def get(self, request, pk):
        try:
            invoice = get_object_or_404(
                Invoice.objects.select_related(
                    "lease__tenant", "lease__unit__property"
                ),
                pk=pk,
            )
            pdf_content = render_to_pdf(
                "invoices/invoice_pdf.html", _invoice_pdf_context(invoice)
            )

            response = HttpResponse(pdf_content, content_type="application/pdf")
            response["Content-Disposition"] = (
                f'attachment; filename="Invoice_{invoice.invoice_number}.pdf"'
            )
            return response
        except Exception as e:
            messages.error(request, f"Failed to generate PDF: {str(e)}")
            return redirect("invoices:invoice_detail", pk=pk)


# invoices/views.py


@login_required
def search_invoices_by_item_description(request):
    """
    Search invoices by item description
    """
    search_term = request.GET.get("q", "")

    invoices = (
        Invoice.objects.filter(items__description__icontains=search_term)
        .distinct()
        .select_related("lease", "lease__tenant")
    )

    context = {
        "invoices": invoices,
        "search_term": search_term,
        "title": f'Search Results for "{search_term}"',
    }
    return render(request, "invoices/invoice_search_results.html", context)


@login_required
@require_POST
def category_create_ajax(request):
    name = (request.POST.get("name") or "").strip()
    if not name:
        return HttpResponseBadRequest("Missing name")
    cat = ItemCategory.objects.filter(name__iexact=name).first()
    created = cat is None
    if cat is None:
        cat = ItemCategory.objects.create(name=name, is_active=True)
    elif not cat.is_active:
        cat.is_active = True
        cat.save(update_fields=["is_active"])
    return JsonResponse({"id": cat.id, "name": cat.name, "created": created})


# views.py (pseudo)


# views.py  (near your other helpers)
def _short(txt, n=15):
    txt = (txt or "").strip()
    return (txt[:n] + "…") if len(txt) > n else txt


def api_properties(request):
    data = list(
        Property.objects.values("id", "property_name").order_by("property_name")
    )
    return JsonResponse(data, safe=False)


def api_units(request):
    """
    GET: property_id=<id>
    Returns: [{'id': unit.id, 'label': 'UnitNumber - Tenant≤25' or 'UnitNumber - Vacant'}]
    """
    prop_id = request.GET.get("property_id")
    Lease = _Lease()

    # Units in property
    units = list(
        Unit.objects.filter(property_id=prop_id)
        .order_by("unit_number")
        .values("id", "unit_number")
    )

    # Active leases to find a current tenant per unit
    leases = Lease.objects.filter(
        unit__property_id=prop_id, status="active"
    ).select_related("tenant", "unit")

    # unit_id -> tenant short name
    def _short(txt, n=25):
        txt = (txt or "").strip()
        return (txt[:n] + "…") if len(txt) > n else txt

    tenant_by_unit = {}
    for l in leases:
        full = (
            " ".join(
                filter(
                    None,
                    [
                        getattr(l.tenant, "first_name", None),
                        getattr(l.tenant, "last_name", None),
                    ],
                )
            ).strip()
            or getattr(l.tenant, "name", "")
            or ""
        )
        tenant_by_unit[l.unit_id] = _short(full, 25)

    out = []
    for u in units:
        tn = tenant_by_unit.get(u["id"], "Vacant")
        out.append({"id": u["id"], "label": f"{u['unit_number']} - {tn}"})

    return JsonResponse(out, safe=False)


def api_leases(request):
    """
    Filters: unit_id and/or tenant_id (both optional).
    Label format: 'Tenant≤25 - Unit  |  Bal: Rs. X,XXX.XX  |  End: YYYY-MM-DD'
    (No lease numbers anywhere.)
    """

    Lease = _Lease()
    unit_id = request.GET.get("unit_id")
    tenant_id = request.GET.get("tenant_id")

    qs = Lease.objects.select_related("tenant", "unit").filter(status="active")

    if unit_id:
        try:
            qs = qs.filter(unit_id=int(unit_id))
        except ValueError:
            return JsonResponse([], safe=False)

    if tenant_id:
        try:
            qs = qs.filter(tenant_id=int(tenant_id))
        except ValueError:
            return JsonResponse([], safe=False)

    def _short(txt, n=25):
        txt = (txt or "").strip()
        return (txt[:n] + "…") if len(txt) > n else txt

    def t_name(l):
        full = (
            " ".join(
                filter(
                    None,
                    [
                        getattr(l.tenant, "first_name", None),
                        getattr(l.tenant, "last_name", None),
                    ],
                )
            ).strip()
            or getattr(l.tenant, "name", "")
            or ""
        )
        return _short(full, 25)

    def unit_label(l):
        u = l.unit
        return getattr(u, "unit_number", None) or getattr(u, "name", "") or ""

    # best-effort balance lookup (fallback to '—' if not on model)
    def lease_balance(l):
        try:
            if hasattr(l, "get_balance") and callable(l.get_balance):
                b = l.get_balance
            else:
                b = getattr(l, "balance", None)
            return f"Rs. {Decimal(b):,.2f}" if b is not None else "—"
        except Exception:
            return "—"

    def lease_end(l):
        try:
            return (getattr(l, "end_date", None) and l.end_date.isoformat()) or ""
        except Exception:
            return ""

    data = []
    for l in qs.order_by("tenant__first_name", "tenant__last_name"):
        # tenant name clipped to 25; unit label as-is
        tshort = t_name(l)  # already 25 chars in your helper
        unit = unit_label(l)

        # add balance and end date if available
        bal = getattr(l, "balance", None)
        end = getattr(l, "end_date", None)
        bal_str = f"{bal:.2f}" if bal is not None else "0.00"
        end_str = end.strftime("%Y-%m-%d") if end else ""

        data.append(
            {
                "id": l.id,  # <-- IMPORTANT: use `id`, not `lease_id`
                "tenant_id": l.tenant_id,
                "label": f"{tshort} - {unit}  |  Bal: {bal_str}  |  End: {end_str}",
            }
        )

    return JsonResponse(data, safe=False)


def api_tenants_for_unit(request):
    """
    Returns distinct active tenants across all properties (or within property_id if provided).
    Shape: [{'id': tenant_id, 'label': 'Tenant≤25 - Unit'}]
    """

    unit_id = request.GET.get("unit_id")
    # we won't use it for initial load, but keep it optional
    property_id = request.GET.get("property_id")
    Lease = _Lease()
    qs = Lease.objects.filter(status="active").select_related("tenant", "unit")

    if property_id and property_id != "all":
        try:
            qs = qs.filter(unit__property_id=int(property_id))
        except (TypeError, ValueError):
            return JsonResponse([], safe=False)

    def _short(txt, n=25):
        txt = (txt or "").strip()
        return (txt[:n] + "…") if len(txt) > n else txt

    def t_name(l):
        full = (
            " ".join(
                filter(
                    None,
                    [
                        getattr(l.tenant, "first_name", None),
                        getattr(l.tenant, "last_name", None),
                    ],
                )
            ).strip()
            or getattr(l.tenant, "name", "")
            or ""
        )
        return _short(full, 25)

    def unit_label(l):
        return getattr(l.unit, "unit_number", None) or getattr(l.unit, "name", "") or ""

    data = [
        {
            "lease_id": l.id,
            "tenant_id": l.tenant_id,
            "label": f"{t_name(l)} - {unit_label(l)}",
        }
        for l in qs.order_by("tenant__first_name", "tenant__last_name")
    ]

    return JsonResponse(data, safe=False)


def api_tenants(request):
    """
    Distinct active tenants (optional by property), label: 'Tenant≤25 - Unit'
    """
    Lease = _Lease()
    property_id = request.GET.get("property_id")

    qs = Lease.objects.select_related("tenant", "unit").filter(status="active")
    if property_id and property_id != "all":
        try:
            pid = int(property_id)
        except ValueError:
            return JsonResponse([], safe=False)
        qs = qs.filter(unit__property_id=pid)

    def _short(txt, n=25):
        txt = (txt or "").strip()
        return (txt[:n] + "…") if len(txt) > n else txt

    seen = {}  # tenant_id -> label
    for l in qs:
        full = (
            " ".join(
                filter(
                    None,
                    [
                        getattr(l.tenant, "first_name", None),
                        getattr(l.tenant, "last_name", None),
                    ],
                )
            ).strip()
            or getattr(l.tenant, "name", "")
            or ""
        )
        tshort = _short(full, 25)
        unit = getattr(l.unit, "unit_number", None) or getattr(l.unit, "name", "") or ""
        if l.tenant_id not in seen:
            seen[l.tenant_id] = f"{tshort} - {unit}"

    out = [{"id": tid, "label": label} for tid, label in seen.items()]
    out.sort(key=lambda r: r["label"].lower())
    return JsonResponse(out, safe=False)


def recurring_list(request):
    # filter by property/unit/lease like before (read GET params)
    qs = RecurringCharge.objects.select_related(
        "lease", "category", "lease__unit", "lease__tenant"
    )
    # apply property/unit/lease filters ...

    def next_run(rc):
        today = timezone.localdate()
        d = date(
            max(today.year, rc.start_date.year),
            max(today.month, rc.start_date.month),
            rc.day_of_month,
        )
        if d < today:  # next month
            month = today.month + 1
            year = today.year + (1 if month > 12 else 0)
            month = 1 if month > 12 else month
            d = date(year, month, rc.day_of_month)
        return d

    rows = [{"rc": rc, "next_run": next_run(rc)} for rc in qs]
    return render(request, "invoices/recurring_list.html", {"rows": rows})


class CategoryListView(ListView):
    model = ItemCategory
    template_name = "invoices/category_list.html"
    paginate_by = 20

    def get_queryset(self):
        qs = super().get_queryset().annotate(item_count=Count("invoiceitem"))
        q = self.request.GET.get("q")
        if q:
            qs = qs.filter(name__icontains=q)

        sort = self.request.GET.get("sort", "name")
        direction = self.request.GET.get("dir", "asc")
        if sort not in {"name"}:
            sort = "name"
        order = sort if direction == "asc" else f"-{sort}"
        return qs.order_by(order, "id")  # stable tiebreaker


class CategoryCreateView(CreateView):
    model = ItemCategory
    fields = ["name", "is_active"]
    template_name = "invoices/category_form.html"
    success_url = reverse_lazy("invoices:category_list")

    def get_success_url(self):
        url = str(self.success_url)
        return f"{url}?embed=1" if self.request.GET.get("embed") == "1" else url


class CategoryUpdateView(UpdateView):
    model = ItemCategory
    fields = ["name", "is_active"]
    template_name = "invoices/category_form.html"
    success_url = reverse_lazy("invoices:category_list")

    def get_success_url(self):
        url = str(self.success_url)
        return f"{url}?embed=1" if self.request.GET.get("embed") == "1" else url


class CategoryDeleteView(DeleteView):
    model = ItemCategory
    template_name = "invoices/category_confirm_delete.html"
    success_url = reverse_lazy("invoices:category_list")

    def get_success_url(self):
        url = str(self.success_url)
        return f"{url}?embed=1" if self.request.GET.get("embed") == "1" else url

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        try:
            return super().post(request, *args, **kwargs)
        except ProtectedError:
            messages.error(
                request,
                "Cannot delete: this category is used by one or more invoice items.",
            )
            return redirect(self.get_success_url())


@require_POST
def category_inline_update(request, pk):
    cat = get_object_or_404(ItemCategory, pk=pk)
    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    name = (data.get("name") or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Name is required."}, status=400)

    # enforce uniqueness (case-insensitive)
    if ItemCategory.objects.exclude(pk=cat.pk).filter(name__iexact=name).exists():
        return JsonResponse(
            {"ok": False, "error": "A category with that name already exists."},
            status=400,
        )

    cat.name = name
    cat.save(update_fields=["name"])
    return JsonResponse({"ok": True, "id": cat.pk, "name": cat.name})


# invoices/views.py


# invoices/views.py


@login_required
@require_POST
def invoice_items_bulk_update(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)

    try:
        payload = json.loads(request.body or "{}")
        items = payload.get("items", [])
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    for it in items:
        item_id = it.get("id")
        if not item_id:
            continue
        item = invoice.items.filter(pk=item_id).first()
        if not item:
            continue

        # Category is a required FK to ItemCategory — create it by name if missing
        cat_name = (it.get("category") or "").strip()
        if cat_name:
            cat, _ = ItemCategory.objects.get_or_create(name=cat_name)
            item.category = cat
        # If blank, leave current category as-is (don’t set None on a required FK)

        item.description = (it.get("description") or "").strip()

        try:
            item.amount = Decimal(str(it.get("amount") or "0"))
        except Exception:
            item.amount = Decimal("0")

        item.save()

    total = invoice.items.aggregate(s=Sum("amount"))["s"] or Decimal("0")
    return JsonResponse({"ok": True, "invoice_total": str(total)})


async def export_invoices_csv(request):
    async def stream():
        yield "Number,Issue Date,Due Date,Amount,Status\n"
        qs = Invoice.objects.select_related("lease").order_by("-issue_date")[:1000]
        for inv in qs:
            yield f"{inv.invoice_number},{inv.issue_date},{inv.due_date},{inv.amount},{inv.status}\n"
            await asyncio.sleep(0)  # let the event loop breathe

    return StreamingHttpResponse(stream(), content_type="text/csv")


@require_POST
def invoice_item_inline_update(request, pk):
    item = get_object_or_404(
        InvoiceItem.objects.select_related("invoice", "category"), pk=pk
    )
    if item.invoice.status == "paid":
        return JsonResponse(
            {
                "ok": False,
                "error": "Paid invoices cannot be changed. Create an adjustment instead.",
            },
            status=400,
        )
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    name = (payload.get("category_name") or "").strip()
    desc = (payload.get("description") or "").strip()
    amt = (payload.get("amount") or "").strip()

    if not name:
        return JsonResponse({"ok": False, "error": "Category is required."}, status=400)
    try:
        amount = Decimal(amt)
        if amount < 0:
            raise InvalidOperation
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid amount."}, status=400)

    # ensure category exists (create on the fly)
    cat, _ = ItemCategory.objects.get_or_create(name=name, defaults={"is_active": True})

    item.category = cat
    item.description = desc
    item.amount = amount
    item.save()

    settlement_end_date = (payload.get("settlement_end_date") or "").strip()
    if settlement_end_date:
        try:
            datetime.strptime(settlement_end_date, "%Y-%m-%d")
        except ValueError:
            return JsonResponse({"ok": False, "error": "Invalid settlement end date."}, status=400)
        marker = f"END_LEASE_MANUAL_ITEM:{item.pk}:{settlement_end_date}"
        if marker not in (item.invoice.notes or ""):
            item.invoice.notes = "\n".join(filter(None, [item.invoice.notes, marker]))
            item.invoice.save(update_fields=["notes", "updated_at"])

    # recompute invoice total (if you already have signals, this is optional)
    total = item.invoice.items.aggregate(t=Sum("amount"))["t"] or Decimal("0.00")

    return JsonResponse(
        {
            "ok": True,
            "id": item.pk,
            "category_name": item.category.name,
            "description": item.description,
            "amount": str(item.amount),
            "invoice_total": str(total),
        }
    )


@login_required
@require_POST
@transaction.atomic
def invoice_item_inline_delete(request, pk):
    item = get_object_or_404(
        InvoiceItem.objects.select_related("invoice", "category"), pk=pk
    )
    invoice = item.invoice

    if invoice.status == "paid":
        return JsonResponse(
            {
                "ok": False,
                "error": "Paid invoice items cannot be deleted. Create an adjustment instead.",
            },
            status=400,
        )

    if item.security_deposit_movements.exists():
        return JsonResponse(
            {
                "ok": False,
                "error": "This item is linked to a security deposit movement and cannot be deleted safely.",
            },
            status=400,
        )

    if invoice.items.count() <= 1:
        return JsonResponse(
            {
                "ok": False,
                "error": "Cannot delete the last invoice line item. Edit it instead or delete the invoice.",
            },
            status=400,
        )

    deleted_id = item.pk
    settlement_end_date = (request.POST.get("settlement_end_date") or "").strip()
    if settlement_end_date:
        try:
            datetime.strptime(settlement_end_date, "%Y-%m-%d")
        except ValueError:
            return JsonResponse({"ok": False, "error": "Invalid settlement end date."}, status=400)
        category_name = item.category.name
        canonical_categories = {
            "maintenance": "Society Maintenance",
            "society maintenance": "Society Maintenance",
            "water": "Water Charges",
            "water charges": "Water Charges",
            "rent": "Rent",
            "internet": "Internet",
        }
        marker_category = (
            "Electricity"
            if category_name.lower().startswith("electric")
            else canonical_categories.get(category_name.lower(), category_name)
        )
        marker = f"END_LEASE_SUPPRESS_CATEGORY:{marker_category}:{settlement_end_date}"
        if marker not in (invoice.notes or ""):
            invoice.notes = "\n".join(filter(None, [invoice.notes, marker]))
            invoice.save(update_fields=["notes", "updated_at"])
    item.delete()

    total = invoice.items.aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
    parts = []
    for it in invoice.items.select_related("category"):
        category_name = getattr(it.category, "name", "") if it.category_id else ""
        desc = (it.description or "").strip()
        if category_name and desc:
            parts.append(f"{category_name}: {desc}")
        elif category_name:
            parts.append(category_name)
        elif desc:
            parts.append(desc)

    invoice.description = ", ".join(parts)
    invoice.amount = total
    invoice.save(update_fields=["description", "amount", "updated_at"])

    return JsonResponse(
        {
            "ok": True,
            "deleted_id": deleted_id,
            "invoice_total": str(total),
            "combined_description": invoice.description or "",
        }
    )


@login_required
@require_POST
def apply_late_fees(request):
    from invoices.late_fees import run_due_late_fee_reminders
    from invoices.models import InvoiceLateFeeReminder

    summary = run_due_late_fee_reminders(
        source=InvoiceLateFeeReminder.SOURCE_MANUAL,
        user=request.user,
    )
    message = (
        "Late-fee run complete: "
        f"{summary['examined']} checked, {summary['processed']} reminders processed, "
        f"{summary['fees_applied']} fees applied, {summary['fees_pending']} pending approval, "
        f"{summary['failed']} failed."
    )
    if summary.get("reason"):
        messages.warning(request, f"{message} {summary['reason']}")
    elif summary["failed"]:
        messages.warning(request, message)
    else:
        messages.success(request, message)
    return redirect("invoices:invoice_list")


# invoices/services.py (new file)


def first_of_month(d):
    return d.replace(day=1)


def ensure_month_invoice(lease, period_date):
    """Return the single invoice object for this lease & period (create if missing)."""
    fallback_due_date = period_date + timedelta(days=7)
    defaults = {
        "due_date": invoice_due_date_from_lease(
            lease, period_date, fallback=fallback_due_date
        ),
        "description": f"Invoice for {period_date:%B %Y}",
        "amount": Decimal("0.00"),
    }
    inv, _ = Invoice.objects.get_or_create(
        lease=lease, issue_date=period_date, defaults=defaults
    )
    return inv


def active_leases_qs():
    """
    Return a queryset of active leases without importing the model at module import time.
    Works whether Lease lives in 'leases' or inside 'invoices'.
    """
    try:
        # preferred if you have a separate 'leases' app
        Lease = apps.get_model("leases", "Lease")
    except LookupError:
        # fallback if Lease is defined in invoices.models
        Lease = apps.get_model("invoices", "Lease")
    return Lease.objects.filter(status="active")


def last_of_month(d: date) -> date:
    return date(d.year, d.month, monthrange(d.year, d.month)[1])


def _active_leases_for_month(month_start: date):
    month_first = first_of_month(month_start)
    month_end = last_of_month(month_first)
    return active_leases_qs().filter(
        start_date__lte=month_end,
    ).filter(Q(end_date__gte=month_first) | Q(end_date__isnull=True))


def apply_fixed_recurring(period_date: date, cutoff_today: bool = False):
    """
    Apply all active FIXED RecurringCharge rows into invoices for the month of `period_date`.

    Rules:
    - Include rows that have started on/before the period's last day.
    - Exclude rows that ended before the period's first day.
    - If cutoff_today=True *and* this is the current month, exclude rows whose end_date < today.
    - For scope LEASE: only that lease. PROPERTY: all leases in that property. GLOBAL: all active leases.
    """
    from .models import (  # local import to avoid import cycles
        InvoiceItem,
        RecurringCharge,
    )

    rules = RecurringCharge.objects.filter(active=True, kind="FIXED").select_related(
        "lease", "property", "category"
    )

    period_first = first_of_month(period_date)
    period_last = last_of_month(period_date)
    today = date.today()
    is_current_month = (
        period_first.year == today.year and period_first.month == today.month
    )

    for rc in rules:
        # Must have started by end of the period
        if rc.start_date and rc.start_date > period_last:
            continue

        # Must not have ended before the beginning of the period (or today, if current month with cutoff)
        if rc.end_date:
            end_cut = today if (cutoff_today and is_current_month) else period_first
            if rc.end_date < end_cut:
                continue

        # Determine target leases
        month_leases = _active_leases_for_month(period_first)
        if rc.scope == "LEASE" and rc.lease_id:
            targets = month_leases.filter(pk=rc.lease_id)
        elif rc.scope == "PROPERTY" and rc.property_id:
            targets = month_leases.filter(unit__property_id=rc.property_id)
        else:  # GLOBAL
            targets = month_leases

        # Post one item per target lease, idempotently
        for lease in targets:
            inv = ensure_month_invoice(lease, period_first)  # invoice date = 1st
            desc = rc.description or (
                rc.category.name if rc.category_id else "Recurring"
            )
            amt = rc.amount or Decimal("0.00")
            InvoiceItem.objects.get_or_create(
                invoice=inv,
                category=rc.category,
                description=desc,
                defaults={"amount": amt, "is_recurring": True},
            )


def post_water_bill(water_bill_id):
    """Split a water bill evenly across active leases in that property and month."""
    wb = WaterBill.objects.select_related("property").get(pk=water_bill_id)
    if wb.posted:
        return  # idempotent

    leases = list(
        _active_leases_for_month(wb.period).filter(unit__property=wb.property)
    )
    if not leases:
        wb.posted = True
        wb.save(update_fields=["posted"])
        return

    # equal split with cent/paisa-safe rounding
    n = len(leases)
    base = (wb.amount / n).quantize(Decimal("0.01"))
    remainder = wb.amount - base * n  # e.g. 0.01..0.04
    steps = int((remainder * 100).copy_abs())  # number of extra paisa
    adjustments = [Decimal("0.00")] * n
    for i in range(steps):
        adjustments[i] += Decimal("0.01") if remainder > 0 else Decimal("-0.01")

    # find or create the category
    water_cat, _ = ItemCategory.objects.get_or_create(name="Water Charges")

    for lease, adj in zip(leases, adjustments):
        inv = ensure_month_invoice(lease, wb.period)
        InvoiceItem.objects.create(
            invoice=inv,
            category=water_cat,
            description=wb.description or f"Water charges {wb.period:%b %Y}",
            amount=base + adj,
        )

    wb.posted = True
    wb.save(update_fields=["posted"])


@transaction.atomic
def run_monthly_billing_for(period_date: date, cutoff_today: bool = False):
    """
    Generate invoices for the month of `period_date`.
    If cutoff_today=True and `period_date` is the *current* month,
    skip recurring rows whose end_date has already passed (end_date < today).
    """
    # 1) Ensure one invoice per active lease (invoice date = 1st of month)
    for lease in _active_leases_for_month(period_date):
        ensure_month_invoice(lease, first_of_month(period_date))

    # 2) Apply fixed recurring rows with optional "current-month cutoff" logic
    apply_fixed_recurring(period_date, cutoff_today=cutoff_today)

    # 3) Post any pending water bills for this period
    for wb in WaterBill.objects.filter(
        period=first_of_month(period_date), posted=False
    ):
        post_water_bill(wb.id)


# invoices/views.py


class RecurringChargeListView(TemplateView):
    template_name = "invoices/recurring_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = date.today()
        selected_lease = self.request.GET.get("lease", "")

        def calc_next_run(rc):
            # honor start/end window and clamp to month length
            base = rc.start_date if rc.start_date > today else today
            day = rc.day_of_month
            last = monthrange(base.year, base.month)[1]
            this = base.replace(day=min(day, last))
            if this < today:
                y = this.year + (1 if this.month == 12 else 0)
                m = 1 if this.month == 12 else (this.month + 1)
                last = monthrange(y, m)[1]
                this = this.replace(year=y, month=m, day=min(day, last))
            if rc.end_date and this > rc.end_date:
                return None
            return this

        qs = RecurringCharge.objects.select_related(
            "lease__tenant", "lease__unit", "property", "category"
        ).order_by("-active", "scope", "start_date")
        if selected_lease:
            qs = qs.filter(lease_id=selected_lease)
        ctx["leases"] = Lease.objects.select_related(
            "tenant", "unit", "unit__property"
        ).order_by(
            "tenant__first_name",
            "tenant__last_name",
            "unit__property__property_name",
            "unit__unit_number",
        )
        ctx["selected_lease"] = selected_lease
        ctx["rows"] = [{"rc": rc, "next_run": calc_next_run(rc)} for rc in qs]
        return ctx


# --- Recurring charges ---

# --- Recurring charges ---


class RecurringChargeCreateView(CreateView):
    model = RecurringCharge
    template_name = "invoices/recurring_form.html"
    # or 'invoices:recurring_list'
    success_url = reverse_lazy("invoices:recurring_wizard")

    def get_form_class(self):
        # lazy import to avoid early model eval
        from .forms import RecurringChargeForm

        return RecurringChargeForm


class RecurringChargeUpdateView(UpdateView):
    model = RecurringCharge
    template_name = "invoices/recurring_form.html"
    # or 'invoices:recurring_list'
    success_url = reverse_lazy("invoices:recurring_wizard")

    def get_form_class(self):
        # lazy import to avoid early model eval
        from .forms import RecurringChargeForm

        return RecurringChargeForm

    def get_queryset(self):
        # optional, but nice for templates/forms
        return RecurringCharge.objects.select_related("lease", "property", "category")


class RecurringChargeDeleteView(DeleteView):
    model = RecurringCharge
    template_name = "confirm_delete.html"
    success_url = reverse_lazy("invoices:recurring_list")


@login_required
def run_billing_current(request):
    """Generate invoices for the current month with end-date cutoff at 'today'."""
    period = first_of_month(date.today())
    run_monthly_billing_for(period, cutoff_today=True)
    messages.success(request, f"Monthly billing generated for {period:%B %Y}.")
    # or 'invoices:recurring_list' if you prefer
    return redirect("invoices:invoice_list")


def run_billing_now(request):
    """
    POST: run billing for a target month.
    If 'month' query arg is provided as YYYY-MM, use that month’s first day.
    Otherwise, default to the first day of next month.
    """
    today = date.today()
    target = request.GET.get("month")
    if target:
        y, m = target.split("-")
        period = date(int(y), int(m), 1)
    else:
        # next month
        y = today.year + (1 if today.month == 12 else 0)
        m = 1 if today.month == 12 else (today.month + 1)
        period = date(y, m, 1)

    run_monthly_billing_for(period)
    messages.success(request, f"Monthly billing prepared for {period:%B %Y}.")
    return redirect("invoices:recurring_list")


# --- Water bills ---


class WaterBillListView(ListView):
    model = WaterBill
    template_name = "invoices/waterbill_list.html"
    context_object_name = "bills"
    paginate_by = 50

    def get_queryset(self):
        return WaterBill.objects.select_related("property").order_by(
            "-period", "property__property_name"
        )


class WaterBillCreateView(CreateView):
    template_name = "invoices/waterbill_form.html"
    success_url = reverse_lazy("invoices:waterbill_list")

    def get_form_class(self):
        from .forms import WaterBillForm  # lazy import!

        return WaterBillForm


class WaterBillUpdateView(UpdateView):
    template_name = "invoices/waterbill_form.html"
    success_url = reverse_lazy("invoices:waterbill_list")

    def get_form_class(self):
        from .forms import WaterBillForm  # lazy import!

        return WaterBillForm

    def get_queryset(self):
        return WaterBill.objects.select_related("property")


def waterbill_post(request, pk):
    bill = get_object_or_404(WaterBill, pk=pk)
    if bill.posted:
        messages.info(request, "This water bill is already posted.")
    else:
        from .services import post_water_bill

        post_water_bill(bill.id)
        messages.success(request, "Water bill posted and split across leases.")
    return redirect("invoices:waterbill_list")


# at the top of the file (near other imports)
# ...
# --- Recurring wizard (compact JSON-backed UI) -------------------------------

# --- Recurring wizard --------------------------------------------------------


# --- Billing Preview & Confirm (current month) --------------------------------


def _Lease():
    try:
        return apps.get_model("leases", "Lease")
    except LookupError:
        return apps.get_model("invoices", "Lease")


def _get_period_invoice(Invoice, lease, period_first):
    """
    Find an existing invoice for a lease at period_first using common field names.
    Returns Invoice or None.
    """
    for field in ("invoice_date", "date", "period"):
        try:
            q = {"lease": lease, field: period_first}
            inv = Invoice.objects.filter(**q).first()
            if inv:
                return inv
        except Exception:
            continue
    return None


def _targets_for_rc(rc, period_first, cutoff_today, filters):
    """
    Yield active target leases for this recurring rule, obeying scope and filters,
    and obeying start/end/cutoff (current-month) rules. Does *not* write.
    """
    Lease = _Lease()

    # Date window
    from calendar import monthrange

    period_last = date(
        period_first.year,
        period_first.month,
        monthrange(period_first.year, period_first.month)[1],
    )
    today = date.today()
    is_current_month = (
        period_first.year == today.year and period_first.month == today.month
    )

    # Start must be on/before period last day
    if rc.start_date and rc.start_date > period_last:
        return []

    # End must not be before period start (or today if cutoff in current month)
    if rc.end_date:
        end_cut = today if (cutoff_today and is_current_month) else period_first
        if rc.end_date < end_cut:
            return []

    # Base targets by scope
    qs = active_leases_qs().select_related("unit", "tenant")  # only active leases
    if rc.scope == "LEASE" and rc.lease_id:
        qs = qs.filter(pk=rc.lease_id)
    elif rc.scope == "PROPERTY" and rc.property_id:
        qs = qs.filter(unit__property_id=rc.property_id)
    else:
        pass  # GLOBAL -> all active leases

    # Apply user filters (optional)
    prop_id = filters.get("property_id")
    unit_id = filters.get("unit_id")
    tenant_id = filters.get("tenant_id")
    lease_id = filters.get("lease_id")
    show_inact = str(filters.get("show_inactive", "0")).lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    active = str(filters.get("active", "0")).lower() in ("1", "true", "yes", "on")

    if prop_id and prop_id != "all":
        try:
            qs = qs.filter(unit__property_id=int(prop_id))
        except ValueError:
            return []
    if unit_id:
        try:
            qs = qs.filter(unit_id=int(unit_id))
        except ValueError:
            return []
    if tenant_id:
        try:
            qs = qs.filter(tenant_id=int(tenant_id))
        except ValueError:
            return []
    if lease_id:  # ← NEW
        try:
            qs = qs.filter(pk=int(lease_id))
        except ValueError:
            return []
    # Default to active-only unless explicitly showing inactive
    if not show_inact:
        if active or ("show_inactive" not in filters):  # keep prior default behavior
            qs = qs.filter(status="active")
    return list(qs)


@require_GET
def api_billing_preview_for(request):
    """
    Preview billing for an arbitrary month: ?month=YYYY-MM
    Same rules as api_billing_preview_current, but for the requested month.
    """
    from .models import Invoice, InvoiceItem, RecurringCharge

    target = request.GET.get("month")
    if not target:
        return HttpResponseBadRequest("Missing 'month' (YYYY-MM)")
    try:
        y, m = map(int, target.split("-", 1))
        period_first = date(y, m, 1)
    except Exception:
        return HttpResponseBadRequest("Invalid 'month' (use YYYY-MM)")

    filters = {
        "property_id": request.GET.get("property_id") or "all",
        "unit_id": request.GET.get("unit_id") or "",
        "tenant_id": request.GET.get("tenant_id") or "",
        "lease_id": request.GET.get("lease_id") or request.GET.get("lease") or "",
        "show_inactive": request.GET.get("show_inactive", "0"),
        "active": request.GET.get("active", ""),
    }
    cutoff_today = False  # for future months, we don't cutoff by today

    leases_map = {}
    grand_total = Decimal("0.00")
    total_items = will_skip_zero = will_skip_dupe = 0

    rules = RecurringCharge.objects.filter(active=True, kind="FIXED").select_related(
        "lease", "property", "category"
    )

    for rc in rules:
        targets = _targets_for_rc(rc, period_first, cutoff_today, filters)
        if not targets:
            continue

        for lease in targets:
            inv = _get_period_invoice(Invoice, lease, period_first)
            desc = rc.description or (
                rc.category.name if rc.category_id else "Recurring"
            )
            amt = rc.amount or Decimal("0.00")
            is_zero = amt <= 0
            is_dupe = False
            if inv:
                is_dupe = InvoiceItem.objects.filter(
                    invoice=inv, category=rc.category, description=desc
                ).exists()

            entry = leases_map.setdefault(
                lease.id,
                {
                    "lease_id": lease.id,
                    "unit_name": getattr(lease.unit, "unit_number", None)
                    or getattr(lease.unit, "name", "")
                    or "",
                    "property_name": getattr(
                        getattr(lease, "unit", None), "property", None
                    )
                    and getattr(lease.unit.property, "property_name", "")
                    or "",
                    "tenant_name": (
                        " ".join(
                            filter(
                                None,
                                [
                                    getattr(lease.tenant, "first_name", None),
                                    getattr(lease.tenant, "last_name", None),
                                ],
                            )
                        ).strip()
                        or getattr(lease.tenant, "name", "")
                        or "—"
                    ),
                    "lease_end_date": (
                        getattr(lease, "end_date", None) and lease.end_date.isoformat()
                    )
                    or "",
                    "items": [],
                    "total": Decimal("0.00"),
                },
            )

            entry["items"].append(
                {
                    "rc_id": rc.id,
                    "category": (rc.category.name if rc.category_id else "—"),
                    "category_id": rc.category_id,
                    "description": desc,
                    "amount": f"{amt:.2f}",
                    "is_zero": is_zero,
                    "is_duplicate": is_dupe,
                    "day_of_month": rc.day_of_month,
                    "start_date": (rc.start_date and rc.start_date.isoformat()) or "",
                    "end_date": (rc.end_date and rc.end_date.isoformat()) or "",
                }
            )

            total_items += 1
            if is_zero:
                will_skip_zero += 1
            elif is_dupe:
                will_skip_dupe += 1
            else:
                entry["total"] += amt

    out = []
    sno = 1
    for _, entry in sorted(
        leases_map.items(),
        key=lambda kv: (kv[1]["unit_name"].lower(), kv[1]["tenant_name"].lower()),
    ):
        entry["sno"] = sno
        sno += 1
        entry["invoice_date"] = period_first.isoformat()
        entry["total"] = f"{entry['total']:.2f}"
        grand_total += Decimal(entry["total"])
        out.append(entry)

    payload = {
        "period": period_first.isoformat(),
        "leases": out,
        "grand_total": f"{grand_total:.2f}",
        "counts": {
            "leases": len(out),
            "items": total_items,
            "skipped_zero_amount": will_skip_zero,
            "skipped_duplicates": will_skip_dupe,
            "billable_items": total_items - will_skip_zero - will_skip_dupe,
        },
    }
    return JsonResponse(payload)


@require_POST
@transaction.atomic
def api_billing_generate_for(request):
    """
    Generate billing for a requested month, optionally for a subset of leases.
    POST JSON: {"month":"YYYY-MM", "lease_ids":[...]}    # lease_ids optional
    - Idempotent via your monthly service and dupe checks
    """
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")
    target = payload.get("month")
    if not target:
        return HttpResponseBadRequest("Missing 'month'")

    try:
        y, m = map(int, target.split("-", 1))
        period = date(y, m, 1)
    except Exception:
        return HttpResponseBadRequest("Invalid 'month' (use YYYY-MM)")

    lease_ids = payload.get("lease_ids") or []
    if lease_ids:
        # Generate only for given leases
        Lease = _Lease()
        from .services import (
            apply_fixed_recurring,
            ensure_month_invoice,
            first_of_month,
        )

        period_first = first_of_month(period)
        # Ensure one invoice per selected lease
        for l in Lease.objects.filter(pk__in=lease_ids, status="active"):
            ensure_month_invoice(l, period_first)
        # Apply fixed recurring for the period (no cutoff)
        apply_fixed_recurring(period_first, cutoff_today=False)
    else:
        # Full month
        run_monthly_billing_for(period, cutoff_today=False)

    return JsonResponse({"ok": True, "period": period.isoformat()})


@require_GET
def api_billing_preview_current(request):
    """
    Preview what *would* be created for the current month.
    Query params (optional): property_id, unit_id, tenant_id
    Rules:
      - one invoice per lease (date = 1st of month)
      - items from RecurringCharge(kind='FIXED', active=1), honoring scope
      - skip rows with amount <= 0.00
      - if current month: end_date must be >= today (your "still within today" rule)
      - show which items would be skipped as duplicates if an identical item already exists
    """
    from .models import Invoice, InvoiceItem, RecurringCharge

    period_first = first_of_month(date.today())
    filters = {
        "property_id": request.GET.get("property_id") or "all",
        "unit_id": request.GET.get("unit_id") or "",
        "tenant_id": request.GET.get("tenant_id") or "",
        "lease_id": request.GET.get("lease_id") or request.GET.get("lease") or "",
        "show_inactive": request.GET.get("show_inactive", "0"),
        "active": request.GET.get("active", ""),
    }
    cutoff_today = True  # as requested for current month generation

    # Build preview grouped by lease
    leases_map = {}  # lease_id -> data
    grand_total = Decimal("0.00")
    total_items = 0
    will_skip_zero = 0
    will_skip_dupe = 0

    rules = RecurringCharge.objects.filter(active=True, kind="FIXED").select_related(
        "lease", "property", "category"
    )

    for rc in rules:
        targets = _targets_for_rc(rc, period_first, cutoff_today, filters)
        if not targets:
            continue

        for lease in targets:
            # find existing invoice (if any) for dupe detection
            inv = _get_period_invoice(Invoice, lease, period_first)

            desc = rc.description or (
                rc.category.name if rc.category_id else "Recurring"
            )
            amt = rc.amount or Decimal("0.00")
            is_zero = amt <= 0

            # Duplicate if an invoice exists AND there is already an item with same (category, description)
            is_dupe = False
            if inv:
                is_dupe = InvoiceItem.objects.filter(
                    invoice=inv, category=rc.category, description=desc
                ).exists()

            entry = leases_map.setdefault(
                lease.id,
                {
                    "lease_id": lease.id,
                    "unit_name": getattr(lease.unit, "unit_number", None)
                    or getattr(lease.unit, "name", "")
                    or "",
                    "property_name": getattr(
                        getattr(lease, "unit", None), "property", None
                    )
                    and getattr(lease.unit.property, "property_name", "")
                    or "",
                    "tenant_name": (
                        " ".join(
                            filter(
                                None,
                                [
                                    getattr(lease.tenant, "first_name", None),
                                    getattr(lease.tenant, "last_name", None),
                                ],
                            )
                        ).strip()
                        or getattr(lease.tenant, "name", "")
                        or "—"
                    ),
                    "lease_end_date": (
                        getattr(lease, "end_date", None) and lease.end_date.isoformat()
                    )
                    or "",
                    "items": [],
                    "total": Decimal("0.00"),
                },
            )

            entry["items"].append(
                {
                    "rc_id": rc.id,
                    "category": (rc.category.name if rc.category_id else "—"),
                    "category_id": rc.category_id,
                    "description": desc,
                    "amount": f"{amt:.2f}",
                    "is_zero": is_zero,
                    "is_duplicate": is_dupe,
                    "day_of_month": rc.day_of_month,
                }
            )

            total_items += 1
            if is_zero:
                will_skip_zero += 1
            elif is_dupe:
                will_skip_dupe += 1
            else:
                entry["total"] += amt

    # finalize totals
    out = []
    sno = 1
    for lease_id, entry in sorted(
        leases_map.items(),
        key=lambda kv: (kv[1]["unit_name"].lower(), kv[1]["tenant_name"].lower()),
    ):
        entry["sno"] = sno
        sno += 1
        entry["invoice_date"] = period_first.isoformat()
        entry["total"] = f"{entry['total']:.2f}"
        grand_total += Decimal(entry["total"])
        out.append(entry)

    payload = {
        "period": period_first.isoformat(),
        "leases": out,
        "grand_total": f"{grand_total:.2f}",
        "counts": {
            "leases": len(out),
            "items": total_items,
            "skipped_zero_amount": will_skip_zero,
            "skipped_duplicates": will_skip_dupe,
            "billable_items": total_items - will_skip_zero - will_skip_dupe,
        },
    }
    return JsonResponse(payload)


@require_POST
@transaction.atomic
def api_billing_generate_current(request):
    """
    Confirm + generate for the current month. Uses the same rules as preview.
    This simply calls your monthly service with the cutoff rule.
    """
    from .services import run_monthly_billing_for

    period_first = first_of_month(date.today())
    run_monthly_billing_for(period_first, cutoff_today=True)
    return JsonResponse({"ok": True, "period": period_first.isoformat()})


# === Next-month (or arbitrary month) preview & generate ===


@require_GET
def api_billing_preview_for(request):
    """
    Preview billing for an arbitrary month: ?month=YYYY-MM
    Same rules as api_billing_preview_current, but for the requested month.
    """
    from .models import Invoice, InvoiceItem, RecurringCharge

    target = request.GET.get("month")
    if not target:
        return HttpResponseBadRequest("Missing 'month' (YYYY-MM)")
    try:
        y, m = map(int, target.split("-", 1))
        period_first = date(y, m, 1)
    except Exception:
        return HttpResponseBadRequest("Invalid 'month' (use YYYY-MM)")

    filters = {
        "property_id": request.GET.get("property_id") or "all",
        "unit_id": request.GET.get("unit_id") or "",
        "tenant_id": request.GET.get("tenant_id") or "",
        "lease_id": request.GET.get("lease_id") or request.GET.get("lease") or "",
        "show_inactive": request.GET.get("show_inactive", "0"),
        "active": request.GET.get("active", ""),
    }
    cutoff_today = False  # future months should not cutoff by 'today'

    leases_map = {}
    grand_total = Decimal("0.00")
    total_items = will_skip_zero = will_skip_dupe = 0

    rules = RecurringCharge.objects.filter(active=True, kind="FIXED").select_related(
        "lease", "property", "category"
    )

    for rc in rules:
        targets = _targets_for_rc(rc, period_first, cutoff_today, filters)
        if not targets:
            continue

        for lease in targets:
            inv = _get_period_invoice(Invoice, lease, period_first)
            desc = rc.description or (
                rc.category.name if rc.category_id else "Recurring"
            )
            amt = rc.amount or Decimal("0.00")
            is_zero = amt <= 0
            is_dupe = False
            if inv:
                is_dupe = InvoiceItem.objects.filter(
                    invoice=inv, category=rc.category, description=desc
                ).exists()

            entry = leases_map.setdefault(
                lease.id,
                {
                    "lease_id": lease.id,
                    "unit_name": getattr(lease.unit, "unit_number", None)
                    or getattr(lease.unit, "name", "")
                    or "",
                    "property_name": getattr(
                        getattr(lease, "unit", None), "property", None
                    )
                    and getattr(lease.unit.property, "property_name", "")
                    or "",
                    "tenant_name": (
                        " ".join(
                            filter(
                                None,
                                [
                                    getattr(lease.tenant, "first_name", None),
                                    getattr(lease.tenant, "last_name", None),
                                ],
                            )
                        ).strip()
                        or getattr(lease.tenant, "name", "")
                        or "—"
                    ),
                    "lease_end_date": (
                        getattr(lease, "end_date", None) and lease.end_date.isoformat()
                    )
                    or "",
                    "items": [],
                    "total": Decimal("0.00"),
                },
            )

            entry["items"].append(
                {
                    "rc_id": rc.id,
                    "category": (rc.category.name if rc.category_id else "—"),
                    "category_id": rc.category_id,
                    "description": desc,
                    "amount": f"{amt:.2f}",
                    "is_zero": is_zero,
                    "is_duplicate": is_dupe,
                    "day_of_month": rc.day_of_month,
                    "start_date": (rc.start_date and rc.start_date.isoformat()) or "",
                    "end_date": (rc.end_date and rc.end_date.isoformat()) or "",
                }
            )

            total_items += 1
            if is_zero:
                will_skip_zero += 1
            elif is_dupe:
                will_skip_dupe += 1
            else:
                entry["total"] += amt

    out = []
    sno = 1
    for _, entry in sorted(
        leases_map.items(),
        key=lambda kv: (kv[1]["unit_name"].lower(), kv[1]["tenant_name"].lower()),
    ):
        entry["sno"] = sno
        sno += 1
        entry["invoice_date"] = period_first.isoformat()
        entry["total"] = f"{entry['total']:.2f}"
        grand_total += Decimal(entry["total"])
        out.append(entry)

    payload = {
        "period": period_first.isoformat(),
        "leases": out,
        "grand_total": f"{grand_total:.2f}",
        "counts": {
            "leases": len(out),
            "items": total_items,
            "skipped_zero_amount": will_skip_zero,
            "skipped_duplicates": will_skip_dupe,
            "billable_items": total_items - will_skip_zero - will_skip_dupe,
        },
    }
    return JsonResponse(payload)


@require_POST
@transaction.atomic
def api_billing_generate_for(request):
    """
    Generate billing for a requested month, optionally for a subset of leases.
    POST JSON: {"month":"YYYY-MM", "lease_ids":[...]}  # lease_ids optional
    """
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    target = payload.get("month")
    if not target:
        return HttpResponseBadRequest("Missing 'month'")
    try:
        y, m = map(int, target.split("-", 1))
        period = date(y, m, 1)
    except Exception:
        return HttpResponseBadRequest("Invalid 'month' (use YYYY-MM)")

    lease_ids = payload.get("lease_ids") or []
    if lease_ids:
        Lease = _Lease()
        from .services import (
            apply_fixed_recurring,
            ensure_month_invoice,
            first_of_month,
        )

        period_first = first_of_month(period)
        for l in Lease.objects.filter(pk__in=lease_ids, status="active"):
            ensure_month_invoice(l, period_first)
        apply_fixed_recurring(period_first, cutoff_today=False)
    else:
        run_monthly_billing_for(period, cutoff_today=False)

    return JsonResponse({"ok": True, "period": period.isoformat()})


class RecurringWizardView(TemplateView):
    template_name = "invoices/recurring_wizard.html"

    def get_context_data(self, **kwargs):
        from .forms import RecurringChargeForm
        from .models import ItemCategory, Property

        ctx = super().get_context_data(**kwargs)
        ctx["properties"] = Property.objects.all().order_by("property_name")
        ctx["categories"] = list(
            ItemCategory.objects.order_by("name").values("id", "name")
        )
        ctx["form"] = RecurringChargeForm()
        return ctx


# --- in views.py (your Recurring Wizard APIs section) ---


def api_recurring_list(request):
    """
    GET params:
      - property_id: int or 'all'
      - active: '1' (default) to restrict to active leases
      - unit_id, tenant_id: optional
      - lease_id: optional -> restrict to a single lease
    """
    from .models import RecurringCharge

    prop_id = request.GET.get("property_id", "all")
    active_only = str(request.GET.get("active", "1")).lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    unit_id = request.GET.get("unit_id")
    tenant_id = request.GET.get("tenant_id")
    lease_id = request.GET.get("lease_id") or request.GET.get("lease")  # accept either

    qs = RecurringCharge.objects.select_related(
        "lease", "lease__unit", "lease__unit__property", "lease__tenant", "category"
    )

    if prop_id not in (None, "", "all"):
        qs = qs.filter(lease__unit__property_id=int(prop_id))
    if unit_id:
        qs = qs.filter(lease__unit_id=int(unit_id))
    if tenant_id:
        qs = qs.filter(lease__tenant_id=int(tenant_id))
    if lease_id:
        qs = qs.filter(lease_id=int(lease_id))

    # Only show active leases when active=1
    if active_only:
        qs = qs.filter(lease__status="active")

    rows = []
    for idx, rc in enumerate(
        qs.order_by("lease__unit__unit_number", "category__name", "id"), start=1
    ):
        tenant = rc.lease.tenant
        tenant_name = (
            " ".join(
                filter(
                    None,
                    [
                        getattr(tenant, "first_name", None),
                        getattr(tenant, "last_name", None),
                    ],
                )
            ).strip()
            or getattr(tenant, "name", "")
            or "—"
        )
        rows.append(
            {
                "sno": idx,
                "id": rc.id,
                "property_id": rc.lease.unit.property_id,
                "property_name": getattr(rc.lease.unit.property, "property_name", ""),
                "lease_id": rc.lease_id,
                "unit_id": rc.lease.unit_id,
                "unit_name": (
                    getattr(rc.lease.unit, "unit_number", None)
                    or getattr(rc.lease.unit, "name", "")
                    or ""
                ),
                "tenant_id": getattr(tenant, "id", None),
                "tenant_name": tenant_name,
                "category_id": rc.category_id,
                "category": rc.category.name if rc.category_id else "—",
                "description": rc.description or "",
                "amount": f"{(rc.amount or Decimal('0.00')):.2f}",
                "day_of_month": rc.day_of_month,
                "start_date": rc.start_date.isoformat() if rc.start_date else "",
                "end_date": rc.end_date.isoformat() if rc.end_date else "",
                "lease_end_date": (
                    rc.lease.end_date.isoformat()
                    if getattr(rc.lease, "end_date", None)
                    else ""
                ),
                "active": rc.active,
            }
        )
    return JsonResponse({"results": rows})


def api_leases_filtered(request):
    """
    GET:
      property_id: int or 'all' (ignored for 'all' to allow global list)
      show: 'active' (default) or 'all'
    Returns: [{id, label, unit_name, tenant_name, start_date, end_date, status}]
    """
    Lease = _Lease()
    prop_id = request.GET.get("property_id", "all")
    show = request.GET.get("show") or "active"  # 'active' | 'all'

    qs = Lease.objects.select_related("unit", "tenant")
    if prop_id != "all":
        qs = qs.filter(unit__property_id=int(prop_id))
    if show != "all":
        qs = qs.filter(status="active")

    def unit_label(l):
        return getattr(l.unit, "unit_number", None) or getattr(l.unit, "name", "") or ""

    today = date.today()
    leases = list(qs)

    # sort by tenant first name (case-insensitive)
    def tenant_first(l):
        return (
            getattr(l.tenant, "first_name", None) or getattr(l.tenant, "name", "") or ""
        ).lower()

    leases.sort(key=tenant_first)

    out = []
    for l in leases:
        first = (
            getattr(l.tenant, "first_name", None)
            or getattr(l.tenant, "name", "")
            or "—"
        )[:20]
        status_txt = "Active"
        if l.status != "active" or (
            getattr(l, "end_date", None) and l.end_date < today
        ):
            status_txt = "Expired"
        out.append(
            {
                "id": l.id,
                # Tenant≤20 — Unit — Status
                "label": f"{first} — {unit_label(l)} — {status_txt}",
                "unit_name": unit_label(l),
                "tenant_name": first,
                "start_date": (
                    l.start_date.isoformat() if getattr(l, "start_date", None) else ""
                ),
                "end_date": (
                    l.end_date.isoformat() if getattr(l, "end_date", None) else ""
                ),
                "status": l.status,
            }
        )
    return JsonResponse({"results": out})


def api_recurring_for_lease(request, lease_id: int):
    """
    Return recurring items strictly from RecurringCharge for a given lease.
    Output rows are suitable for inline editing (category_id, amount, day_of_month, dates, active, description).
    """
    from .models import RecurringCharge

    Lease = _Lease()
    try:
        Lease.objects.only("id").get(pk=lease_id)
    except Lease.DoesNotExist:
        return JsonResponse({"results": []})

    rows = []
    qs = (
        RecurringCharge.objects.filter(lease_id=lease_id)
        .select_related("category")
        .order_by("category__name", "description")
    )
    for rc in qs:
        rows.append(
            {
                "id": rc.id,
                "category_id": rc.category_id,
                "category": rc.category.name if rc.category_id else "—",
                "description": rc.description or "",
                "amount": f"{(rc.amount or Decimal('0.00')):.2f}",
                "day_of_month": rc.day_of_month,
                "start_date": rc.start_date.isoformat() if rc.start_date else "",
                "end_date": rc.end_date.isoformat() if rc.end_date else "",
                "active": rc.active,
            }
        )
    return JsonResponse({"results": rows})


@require_POST
def api_recurring_update(request, pk: int):
    from .models import ItemCategory, RecurringCharge

    rc = get_object_or_404(RecurringCharge, pk=pk)
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("Invalid JSON")

    def to_decimal(v):
        if v in (None, ""):
            return Decimal("0.00")
        return Decimal(str(v))

    def to_date(v):
        if not v:
            return None
        return datetime.strptime(v, "%Y-%m-%d").date()

    if "category_id" in data:
        cat_id = data.get("category_id") or None
        if cat_id and not ItemCategory.objects.filter(pk=cat_id).exists():
            return HttpResponseBadRequest("Invalid category_id")
        rc.category_id = cat_id

    for key in ("description",):
        if key in data:
            setattr(rc, key, data.get(key) or "")

    if "amount" in data:
        rc.amount = to_decimal(data.get("amount"))
    if "day_of_month" in data:
        try:
            rc.day_of_month = max(1, min(31, int(data.get("day_of_month") or 1)))
        except ValueError:
            return HttpResponseBadRequest("Invalid day_of_month")
    if "start_date" in data:
        rc.start_date = to_date(data.get("start_date"))
    if "end_date" in data:
        rc.end_date = to_date(data.get("end_date"))
    if "active" in data:
        rc.active = bool(data.get("active"))

    rc.save()
    return JsonResponse({"ok": True})


@require_POST
def api_recurring_delete(request, pk: int):
    from .models import RecurringCharge

    rc = get_object_or_404(RecurringCharge, pk=pk)
    rc.delete()
    return JsonResponse({"ok": True})


@require_POST
def api_recurring_create(request):
    from .models import ItemCategory, RecurringCharge

    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("Invalid JSON")

    logger.info("api_recurring_create called with data=%s", data)

    lease_id = data.get("lease_id")
    if not lease_id:
        return HttpResponseBadRequest("lease_id is required")

    Lease = _Lease()
    try:
        lease = Lease.objects.get(pk=int(lease_id))
    except Exception:
        return HttpResponseBadRequest("Invalid lease_id")

    # optional/nullable inputs
    cat_id = data.get("category_id") or None
    if cat_id and not ItemCategory.objects.filter(pk=cat_id).exists():
        return HttpResponseBadRequest("Invalid category_id")

    def to_decimal(v):
        if v in (None, ""):
            return Decimal("0.00")
        return Decimal(str(v))

    def to_date(v):
        if not v:
            return None
        return datetime.strptime(v, "%Y-%m-%d").date()

    rc = RecurringCharge.objects.create(
        lease=lease,
        property=lease.unit.property,
        category_id=cat_id,
        description=data.get("description") or "",
        amount=to_decimal(data.get("amount")),
        day_of_month=int(data.get("day_of_month") or 1),
        start_date=to_date(data.get("start_date")),
        end_date=to_date(data.get("end_date")),
        active=bool(data.get("active", True)),
        scope="LEASE",  # consistent with wizard use
        kind="FIXED",  # consistent with wizard use
    )

    return JsonResponse({"ok": True, "id": rc.id})


@require_POST
def api_recurring_backfill(request, pk: int):
    from .services import backfill_recurring_to_invoices

    try:
        posted = backfill_recurring_to_invoices(pk)
    except Exception as e:
        return HttpResponseBadRequest(str(e))
    return JsonResponse({"ok": True, "posted_items": posted})


def api_units_for_property(request):
    """
    GET: property_id=all|<int>, active=1|0
    Returns units (id, label 'Unit — Tenant') that have (optionally active) leases.
    """
    Lease = _Lease()
    prop_id = request.GET.get("property_id", "all")
    active_only = str(request.GET.get("active", "1")).lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    qs = Lease.objects.select_related("unit", "tenant")
    if prop_id != "all":
        try:
            qs = qs.filter(unit__property_id=int(prop_id))
        except ValueError:
            return HttpResponseBadRequest("Invalid property_id")
    if active_only:
        qs = qs.filter(status="active")

    def unit_label(l):
        return getattr(l.unit, "unit_number", None) or getattr(l.unit, "name", "") or ""

    out = []
    seen_units = set()
    for l in qs:
        uid = l.unit_id
        if uid in seen_units:
            continue
        seen_units.add(uid)
        tenant_full = (
            " ".join(
                filter(
                    None,
                    [
                        getattr(l.tenant, "first_name", None),
                        getattr(l.tenant, "last_name", None),
                    ],
                )
            ).strip()
            or getattr(l.tenant, "name", "")
            or "—"
        )
        out.append(
            {
                "id": uid,
                "label": f"{unit_label(l)} — {tenant_full}",
            }
        )
    # sort by unit label
    out.sort(key=lambda r: (r["label"] or "").lower())
    return JsonResponse({"results": out})


def api_tenants_for_property(request):
    """
    GET: property_id=all|<int>, active=1|0
    Returns tenants (id, label 'Tenant — Unit') for leases in scope.
    """
    Lease = _Lease()
    prop_id = request.GET.get("property_id", "all")
    active_only = str(request.GET.get("active", "1")).lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    qs = Lease.objects.select_related("unit", "tenant")
    if prop_id != "all":
        try:
            qs = qs.filter(unit__property_id=int(prop_id))
        except ValueError:
            return HttpResponseBadRequest("Invalid property_id")
    if active_only:
        qs = qs.filter(status="active")

    def unit_label(l):
        return getattr(l.unit, "unit_number", None) or getattr(l.unit, "name", "") or ""

    out = []
    for l in qs:
        tid = getattr(l.tenant, "id", None)
        if tid is None:
            continue
        tenant_full = (
            " ".join(
                filter(
                    None,
                    [
                        getattr(l.tenant, "first_name", None),
                        getattr(l.tenant, "last_name", None),
                    ],
                )
            ).strip()
            or getattr(l.tenant, "name", "")
            or "—"
        )
        out.append(
            {
                "id": tid,
                "label": f"{tenant_full} — {unit_label(l)}",
            }
        )
    # de-dupe by (id,label)
    unique = {(r["id"], r["label"]): r for r in out}.values()
    out = sorted(unique, key=lambda r: (r["label"] or "").lower())
    return JsonResponse({"results": out})


# invoices/views.py


@login_required
@require_POST
@transaction.atomic
def api_invoices_bulk_delete_for(request):
    """
    POST JSON:
    {
      "month": "YYYY-MM",
      "property_id": null|int,
      "lease_ids": [..],              # optional
      "only_unpaid": true,            # skip paid/part-paid
      "only_auto": true               # skip invoices with manual items
    }
    """
    import json

    from .models import Invoice

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    month = (payload.get("month") or "").strip()
    if not month:
        return HttpResponseBadRequest("Missing 'month'")
    try:
        y, m = [int(x) for x in month.split("-", 1)]
        period_first = date(y, m, 1)
    except Exception:
        return HttpResponseBadRequest("Invalid 'month' (use YYYY-MM)")

    # adjust if your field differs
    qs = Invoice.objects.filter(issue_date=period_first)

    # Optional filters
    if payload.get("property_id"):
        qs = qs.filter(lease__unit__property_id=payload["property_id"])
    if payload.get("lease_ids"):
        qs = qs.filter(lease_id__in=payload["lease_ids"])

    # Safety rails
    if payload.get("only_unpaid", True):
        # adjust to your statuses
        qs = qs.exclude(status__in=["paid", "partially_paid"])

    if payload.get("only_auto", True):
        # keep invoices that have ONLY auto/recurring items
        auto_ids = []
        for inv in qs.select_related("lease").prefetch_related("items"):
            items = list(inv.items.all())
            # Treat an invoice as auto-generated if every item looks recurring
            # (your items have is_recurring flag in code paths for recurring; fall back to description/category if needed)
            only_auto = items and all(
                getattr(it, "is_recurring", False) for it in items
            )
            if only_auto:
                auto_ids.append(inv.id)
        qs = qs.filter(id__in=auto_ids)

    count = qs.count()
    qs.delete()  # cascades to InvoiceItem

    return JsonResponse(
        {"ok": True, "deleted": count, "period": period_first.isoformat()}
    )


# invoices/views.py


@login_required
@require_POST
@transaction.atomic
def invoices_bulk_delete(request):
    from .models import Invoice

    ids = request.POST.getlist("ids") or request.POST.getlist(
        "select"
    )  # django-tables2 posts 'select'
    if not ids:
        messages.error(request, "No invoices selected.")
        return redirect("invoices:invoice_list")

    qs = Invoice.objects.filter(id__in=ids)

    # Safety rails
    # adjust to your statuses
    qs = qs.exclude(status__in=["paid", "partially_paid"])

    deleted = qs.count()
    qs.delete()
    messages.success(request, f"Deleted {deleted} invoice(s).")
    return redirect("invoices:invoice_list")


# invoices/views.py


# adjust to your enum/choices
SAFETY_EXCLUDE_STATUSES = ["paid", "partially_paid"]


def _parse_month(yyyy_mm: str) -> date:
    y, m = [int(x) for x in (yyyy_mm or "").split("-", 1)]
    return date(y, m, 1)


def _ids_from_request(request):
    """
    Accept both our own 'ids'[] and django-tables2 CheckBoxColumn default 'select'[].
    """
    ids = (
        request.POST.getlist("ids")
        or request.GET.getlist("ids")
        or request.POST.getlist("select")
    )
    # de-dup + sanitize to ints
    try:
        ids = list({int(x) for x in ids})
    except Exception:
        ids = []
    return ids


@login_required
@require_http_methods(["GET", "POST"])
def invoices_bulk_delete_preview(request):
    """
    Two modes:
      - Month mode (Option A): GET ?month=YYYY-MM  [+ optional property_id etc if you add later]
      - IDs mode (Option B):  POST with ids[]=...
    Renders a preview of what *would* be deleted.
    """
    invoices_qs = Invoice.objects.none()
    mode = None
    month = None
    ids = []

    if request.method == "GET" and request.GET.get("month"):
        # Option A preview
        month = _parse_month(request.GET.get("month"))
        invoices_qs = Invoice.objects.filter(issue_date=month).exclude(
            status__in=SAFETY_EXCLUDE_STATUSES
        )
        mode = "month"
    else:
        # Option B preview (from selected checkboxes)
        ids = _ids_from_request(request)
        if not ids:
            messages.error(request, "No invoices selected.")
            return redirect("invoices:invoice_list")
        invoices_qs = Invoice.objects.filter(pk__in=ids).exclude(
            status__in=SAFETY_EXCLUDE_STATUSES
        )
        mode = "ids"

    # inside invoices_bulk_delete_preview() before select_related/prefetch
    invoices_qs = invoices_qs.order_by(
        "lease__unit__property__property_name",
        "lease__unit__unit_number",  # change to "lease__unit__number" if that's your field
        "issue_date",
        "id",
    )

    # Preload relations for template: lease → unit → property, plus items
    invoices_qs = invoices_qs.select_related("lease__unit__property").prefetch_related(
        "items"
    )

    # Compute totals once (don’t trust any stale cached amount fields)
    rows = []
    for inv in invoices_qs:
        total = inv.items.aggregate(t=Sum("amount"))["t"] or 0
        rows.append({"inv": inv, "total": total})

    context = {
        "mode": mode,
        "month": month,  # date object or None
        "ids": ids,  # list or []
        "rows": rows,  # [{inv, total}]
        "count": len(rows),
    }
    return render(request, "invoices/invoices_bulk_delete_preview.html", context)


@login_required
@require_POST
def invoices_bulk_delete_confirm(request):
    """
    Final step: actually delete after preview.
    - If 'month' is present → delete by month (Option A)
    - Else if 'ids' present → delete those IDs (Option B)
    """
    deleted = 0
    if request.POST.get("month"):
        # Option A
        month = _parse_month(request.POST.get("month"))
        qs = Invoice.objects.filter(issue_date=month).exclude(
            status__in=SAFETY_EXCLUDE_STATUSES
        )
        deleted = qs.count()
        qs.delete()
        messages.success(request, f"Deleted {deleted} invoice(s) for {month:%B %Y}.")
    else:
        # Option B
        ids = _ids_from_request(request)
        if not ids:
            messages.error(request, "No invoices selected.")
            return redirect("invoices:invoice_list")
        qs = Invoice.objects.filter(pk__in=ids).exclude(
            status__in=SAFETY_EXCLUDE_STATUSES
        )
        deleted = qs.count()
        qs.delete()
        messages.success(request, f"Deleted {deleted} selected invoice(s).")

    return redirect("invoices:invoice_list")


# invoices/views.py


def build_invoice_whatsapp_message(inv):
    lease = getattr(inv, "lease", None)
    tenant = getattr(lease, "tenant", None)
    unit = getattr(lease, "unit", None)
    prop = getattr(unit, "property", None)

    tenant_name = (
        " ".join(
            filter(
                None,
                [getattr(tenant, "first_name", ""), getattr(tenant, "last_name", "")],
            )
        ).strip()
        or "Customer"
    )
    amount = getattr(inv, "amount", None)
    due_date = getattr(inv, "due_date", None)

    # If you have line items, you can expand here; keep it short for WhatsApp by default
    # items = getattr(inv, "items", None)
    # items_text = "\n".join([f"- {i.description}: Rs.{i.amount:.2f}" for i in items.all()]) if hasattr(items, "all") else ""

    detail_url = reverse("invoices:invoice_detail", args=[inv.pk])

    lines = [
        f"Dear {tenant_name},",
        "",
        f"*Invoice #{getattr(inv, 'invoice_number', inv.pk)}*",
        f"Amount: Rs.{(amount or 0):,.2f}",
        f"Due Date: {due_date:%b %d, %Y}" if due_date else "",
        f"Property: {getattr(prop, 'property_name', '')}",
        f"Unit: {getattr(unit, 'unit_number', '')}",
        "",
        "View details here:",
        # type: ignore[name-defined]  # request is available in view below
        build_public_path_url(detail_url),
        "",
        "Thank you!",
    ]
    return "\n".join([l for l in lines if l])  # drop empty rows


@login_required
@require_GET
def invoice_whatsapp_message(request, pk: int):
    inv = (
        Invoice.objects.select_related(
            "lease", "lease__tenant", "lease__unit", "lease__unit__property"
        )
        .filter(pk=pk)
        .first()
    )
    if not inv:
        return JsonResponse({"error": "Invoice not found"}, status=404)

    # Prefer tenant phone on the lease; customize if you store phone elsewhere
    phone = getattr(getattr(getattr(inv, "lease", None), "tenant", None), "phone", "")
    message = build_invoice_whatsapp_message(request, inv)

    return JsonResponse(
        {
            "phone": phone or "",
            "message": message or "",
            "invoice_id": inv.pk,
            "invoice_number": getattr(inv, "invoice_number", inv.pk),
        }
    )


# adjust import to your model location


def build_invoice_whatsapp_message(request, inv):
    lease = getattr(inv, "lease", None)
    tenant = getattr(lease, "tenant", None)
    unit = getattr(lease, "unit", None)
    prop = getattr(unit, "property", None)

    tenant_name = (
        " ".join(
            filter(
                None,
                [getattr(tenant, "first_name", ""), getattr(tenant, "last_name", "")],
            )
        ).strip()
        or "Customer"
    )

    num = getattr(inv, "invoice_number", inv.pk)
    amount = getattr(inv, "amount", 0) or 0
    lease_balance = (
        getattr(lease, "get_balance", Decimal("0.00")) if lease else Decimal("0.00")
    )
    security_balance = (
        getattr(lease, "security_balance_to_collect", Decimal("0.00"))
        if lease
        else Decimal("0.00")
    )
    total_balance = (lease_balance or Decimal("0.00")) + (
        security_balance or Decimal("0.00")
    )
    status = (
        inv.get_status_display()
        if hasattr(inv, "get_status_display")
        else getattr(inv, "status", "")
    )
    due = getattr(inv, "due_date", None)
    issue = getattr(inv, "issue_date", None)
    currency_symbol = getattr(settings, "CURRENCY_SYMBOL", "Rs.")
    if not currency_symbol:
        currency_symbol = "Rs."
    public_token = make_public_invoice_token(inv.pk)
    public_url = build_public_url(
        "invoices:public_invoice_detail", args=[public_token]
    )

    items = []
    for index, item in enumerate(inv.items.select_related("category").all(), start=1):
        category = getattr(getattr(item, "category", None), "name", "") or ""
        description = (getattr(item, "description", "") or "").strip()
        label = category or description or "Item"
        detail = description or category or "Item"
        item_amount = getattr(item, "amount", 0) or 0
        items.append(
            f"{index}. {label} — {detail} — {currency_symbol} {float(item_amount):,.2f}"
        )
    items_text = "\n".join(items) if items else "—"

    security_lines = []
    if security_balance:
        security_lines.append(
            f"Security Deposit Balance: {currency_symbol} {float(security_balance or 0):,.2f}"
        )

    lines = [
        f"Dear {tenant_name},",
        f"INVOICE #{num}",
    ]
    date_line = " | ".join(
        [
            part
            for part in [
                f"Date: {issue:%b %d, %Y}" if issue else "",
                f"Due: {due:%b %d, %Y}" if due else "",
            ]
            if part
        ]
    )
    if date_line:
        lines.append(date_line)
    lines.extend(
        [
            "",
            f"Property / Unit: {getattr(prop, 'property_name', '')}-{getattr(unit, 'unit_number', '')}",
            f"Status: {status} | Lease Bal: {currency_symbol} {float(lease_balance or 0):,.2f}",
        ]
    )
    lines.extend(security_lines)
    lines.extend(
        [
            "",
            "Items:",
            items_text,
            "",
            f"Total: {currency_symbol} {float(amount):,.2f}",
            f"New Balance: * {currency_symbol} {float(total_balance or 0):,.2f}*",
            "",
            "View invoice:",
            public_url,
            "",
            "Thank you!",
        ]
    )
    return "\n".join(lines)


@login_required
@require_GET
def api_invoice_whatsapp(request, pk: int):
    inv = (
        Invoice.objects.select_related(
            "lease", "lease__tenant", "lease__unit", "lease__unit__property"
        )
        .filter(pk=pk)
        .first()
    )
    if not inv:
        raise Http404("Invoice not found")

    phone = (
        getattr(getattr(getattr(inv, "lease", None), "tenant", None), "phone", "") or ""
    )
    message = build_invoice_whatsapp_message(request, inv)
    return JsonResponse({"phone": phone, "message": message, "invoice_id": inv.pk})


# leases/views.py
# invoices/views.py


class LeaseSecurityMixin:
    """
    Helper mixin for security-deposit views.
    Expects URL kwarg 'lease_id' (e.g. /leases/<lease_id>/security/…)
    and exposes self.lease + common context + success_url.
    """

    lease_url_kwarg = "lease_id"

    def dispatch(self, request, *args, **kwargs):
        lease_pk = kwargs.get(self.lease_url_kwarg) or kwargs.get("pk")
        self.lease = get_object_or_404(Lease, pk=lease_pk)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # ensure lease + security summary always in context
        ctx.setdefault("lease", self.lease)
        ctx.setdefault("sec_totals", security_deposit_totals(self.lease))
        return ctx

    def get_success_url(self):
        # all create/update/delete views will redirect back to the list
        return reverse(
            "invoices:security_deposit_list", kwargs={"lease_id": self.lease.pk}
        )


class SecurityDepositListView(LeaseSecurityMixin, ListView):
    model = SecurityDepositTransaction
    template_name = "leases/security_deposit_list.html"
    context_object_name = "deposit_transactions"

    def get_queryset(self):
        return SecurityDepositTransaction.objects.filter(lease=self.lease).order_by(
            "date", "id"
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        rows = []
        balance = Decimal("0.00")
        ZERO = Decimal("0.00")

        for tx in ctx["deposit_transactions"]:
            amt = tx.amount or ZERO

            # --- decide signed amount for display ---
            if tx.type == "PAYMENT":
                # money coming IN to deposit
                signed_amt = amt  # +ve
                balance += amt  # increase held
            elif tx.type in ("REFUND", "DAMAGE"):
                # money going OUT from deposit
                signed_amt = -amt  # show as negative
                balance -= amt  # decrease held
            elif tx.type == "ADJUST":
                signed_amt = amt
            else:
                # REQUIRED or anything else → does not change current held,
                # but we still show as positive line item
                signed_amt = amt

            rows.append(
                {
                    "obj": tx,
                    "date": tx.date,
                    "type": tx.get_type_display(),
                    "raw_type": tx.type,
                    "description": tx.notes or "",
                    "amount": signed_amt,
                    "balance": balance,
                }
            )

        ctx["ledger_rows"] = rows
        return ctx


from django.views.decorators.http import require_GET

from invoices.models import SecurityDepositTransaction
from invoices.services import security_deposit_totals


@require_GET
def api_security_receipt_whatsapp(request, pk):
    tx = get_object_or_404(
        SecurityDepositTransaction.objects.select_related(
            "lease__tenant", "lease__unit__property"
        ),
        pk=pk,
    )

    lease = tx.lease
    tenant = lease.tenant
    unit = lease.unit
    prop = unit.property

    totals = security_deposit_totals(lease)

    # Make tx amount signed like your ledger logic
    amt = tx.amount or 0
    if tx.type in ("REFUND", "DAMAGE"):
        amt = -amt

    # Status
    status = "Pending" if (totals.get("balance_to_collect") or 0) > 0 else "Paid"

    payload = {
        "type": tx.type,
        "phone": getattr(tenant, "phone", "") or "",
        "tenantName": getattr(tenant, "first_name", "") or "Customer",
        "propertyName": getattr(prop, "property_name", "") or "",
        "unitNumber": getattr(unit, "unit_number", "") or "",
        "periodStart": lease.start_date.strftime("%b %d, %Y")
        if lease.start_date
        else "",
        "periodEnd": lease.end_date.strftime("%b %d, %Y") if lease.end_date else "",
        "securityRequired": float(totals.get("required") or 0),
        "securityStatus": status,
        "secBalToCollect": float(totals.get("balance_to_collect") or 0),
        "tranDate": tx.date.strftime("%b %d, %Y") if tx.date else "",
        "txAmount": float(amt or 0),
        "leaseBalance": float(
            getattr(lease, "get_balance", 0)()
            if callable(getattr(lease, "get_balance", 0))
            else getattr(lease, "get_balance", 0) or 0
        ),
    }
    return JsonResponse(payload)


# invoices/views.py
from django.views.decorators.http import require_GET

from invoices.models import SecurityDepositTransaction
from invoices.services import build_security_receipt_message


@require_GET
def api_security_receipt_whatsapp(request, pk: int):
    tx = get_object_or_404(
        SecurityDepositTransaction.objects.select_related(
            "lease__tenant", "lease__unit__property"
        ),
        pk=pk,
    )

    phone = getattr(tx.lease.tenant, "phone", "") or ""
    message = build_security_receipt_message(request, tx)
    return JsonResponse({"phone": phone, "message": message, "security_tx_id": tx.pk})


# ---------- Monthly billing run review ----------
from invoices.billing_queue import enqueue_billing_job
from invoices.models import BillingProgressJob, MonthlyBillingRun, MonthlyBillingRunItem
from invoices.services import (
    _invoice_water_item,
    _latest_meter_reading_for_lease,
    _tenant_name,
    approve_monthly_billing_electric_reading,
    audit_electric_posting_inconsistencies,
    exclude_monthly_billing_item,
    generate_monthly_billing_electric,
    generate_monthly_billing_invoices,
    generate_monthly_billing_pdf_for_item,
    generate_monthly_billing_pdfs,
    parse_billing_month,
    prepare_monthly_billing_ready,
    prepare_monthly_billing_ready_for_item,
    resolve_monthly_billing_water,
    rollback_monthly_billing_item,
    rollback_monthly_billing_run,
    run_monthly_billing_dry_run,
    run_monthly_billing_full,
    run_monthly_billing_engine,
    run_monthly_billing_preflight,
    send_monthly_billing_item,
    send_monthly_billing_ready,
)


class MonthlyBillingRunListView(LoginRequiredMixin, ListView):
    model = MonthlyBillingRun
    template_name = "invoices/monthly_billing_run_list.html"
    context_object_name = "runs"
    paginate_by = 24

    def get_queryset(self):
        return MonthlyBillingRun.objects.order_by("-billing_month", "-created_at")


class MonthlyBillingRunDetailView(LoginRequiredMixin, DetailView):
    model = MonthlyBillingRun
    template_name = "invoices/monthly_billing_run_detail.html"
    context_object_name = "run"

    def get_queryset(self):
        return MonthlyBillingRun.objects.prefetch_related(
            "items__lease__tenant",
            "items__lease__unit__property",
            "items__invoice",
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        items_qs = self.object.items.select_related(
            "lease__tenant",
            "lease__unit__property",
            "tenant",
            "property",
            "unit",
            "invoice",
        )
        items = list(items_qs)
        electric_period_end = None
        water_item_by_invoice = {}
        for item in items:
            item.is_smart_meter_unit = bool(
                getattr(item.unit, "is_smart_meter", False)
                or getattr(getattr(item.lease, "unit", None), "is_smart_meter", False)
            )
            item.water_billable = bool(
                item.lease_id and getattr(item.lease, "bill_water_charges", False)
            )
            water_item = _invoice_water_item(item.invoice)
            item.water_invoice_item = water_item
            item.water_display_amount = getattr(water_item, "amount", None)
            item.water_display_description = (
                getattr(water_item, "description", "")
                or f"Water charges {self.object.billing_month:%b %Y}"
            )
            if item.invoice_id:
                water_item_by_invoice[item.invoice_id] = water_item
            if item.electric_required and item.lease_id and item.electric_period_end:
                electric_period_end = item.electric_period_end
                break
        for item in items:
            if not hasattr(item, "water_billable"):
                item.is_smart_meter_unit = bool(
                    getattr(item.unit, "is_smart_meter", False)
                    or getattr(
                        getattr(item.lease, "unit", None), "is_smart_meter", False
                    )
                )
                item.water_billable = bool(
                    getattr(item.lease, "bill_water_charges", True)
                )
                water_item = (
                    water_item_by_invoice.get(item.invoice_id)
                    if item.invoice_id
                    else None
                )
                if item.invoice_id not in water_item_by_invoice:
                    water_item = _invoice_water_item(item.invoice)
                item.water_invoice_item = water_item
                item.water_display_amount = getattr(water_item, "amount", None)
                item.water_display_description = (
                    getattr(water_item, "description", "")
                    or f"Water charges {self.object.billing_month:%b %Y}"
                )
        if electric_period_end:
            for item in items:
                if item.electric_required and item.lease_id:
                    latest, _installations = _latest_meter_reading_for_lease(
                        item.lease, electric_period_end
                    )
                    item.latest_meter_reading_at = (
                        timezone.localtime(latest.ts) if latest else None
                    )
        billable_items = items_qs.exclude(
            status__in=[
                MonthlyBillingRunItem.STATUS_SKIPPED,
                MonthlyBillingRunItem.STATUS_EXCLUDED,
            ]
        )
        electric_items = billable_items.filter(unit__is_smart_meter=True)
        water_items = [
            row
            for row in billable_items.select_related("lease", "invoice")
            if row.lease_id and getattr(row.lease, "bill_water_charges", False)
        ]
        water_resolved_count = sum(
            1 for item in water_items if _invoice_water_item(item.invoice)
        )
        ended_recently_start = self.object.billing_month - timedelta(days=30)
        ended_recently_end = self.object.billing_month - timedelta(days=1)
        ended_recently_leases = (
            Lease.objects.filter(
                end_date__gte=ended_recently_start,
                end_date__lte=ended_recently_end,
            )
            .select_related("tenant", "unit__property")
            .order_by("end_date", "unit__property__property_name", "unit__unit_number")
        )
        ctx["billing_kpis"] = {
            "billable_count": billable_items.count(),
            "recurring_found_count": billable_items.filter(
                recurring_invoice_found=True
            ).count(),
            "electric_total_count": electric_items.count(),
            "electric_pending_count": electric_items.filter(
                electric_ready=False,
                status=MonthlyBillingRunItem.STATUS_PENDING,
            ).count(),
            "electric_ready_count": electric_items.filter(electric_ready=True).count(),
            "water_required_count": len(water_items),
            "water_resolved_count": water_resolved_count,
            "ended_recently_count": ended_recently_leases.count(),
        }
        ctx["ended_recently_window_start"] = ended_recently_start
        ctx["ended_recently_window_end"] = ended_recently_end
        ctx["ended_recently_leases"] = ended_recently_leases

        ctx["tabs"] = [
            (
                "needs_attention",
                "Needs Attention",
                [
                    item
                    for item in items
                    if item.status == MonthlyBillingRunItem.STATUS_PENDING
                ],
            ),
            (
                "not_validated",
                "Not Yet Validated",
                [
                    item
                    for item in items
                    if item.status == MonthlyBillingRunItem.STATUS_DRAFT
                ],
            ),
            (
                "ready_to_send",
                "Ready to Send",
                [
                    item
                    for item in items
                    if item.status == MonthlyBillingRunItem.STATUS_READY
                ],
            ),
            (
                "sending",
                "Sending / PDF Ready",
                [
                    item
                    for item in items
                    if item.status == MonthlyBillingRunItem.STATUS_READY
                    and item.invoice_pdf
                ],
            ),
            (
                "sent",
                "Sent",
                [
                    item
                    for item in items
                    if item.status == MonthlyBillingRunItem.STATUS_SENT
                ],
            ),
            (
                "failed",
                "Failed",
                [
                    item
                    for item in items
                    if item.status == MonthlyBillingRunItem.STATUS_FAILED
                ],
            ),
            (
                "excluded",
                "Excluded",
                [
                    item
                    for item in items
                    if item.status == MonthlyBillingRunItem.STATUS_EXCLUDED
                ],
            ),
            (
                "rolled_back",
                "Rolled Back",
                [
                    item
                    for item in items
                    if item.status == MonthlyBillingRunItem.STATUS_ROLLED_BACK
                ],
            ),
            (
                "skipped",
                "Skipped",
                [
                    item
                    for item in items
                    if item.status == MonthlyBillingRunItem.STATUS_SKIPPED
                ],
            ),
        ]
        ctx["all_items"] = items
        ctx["electric_audit_rows"] = audit_electric_posting_inconsistencies(
            self.object.billing_month
        )
        return ctx


@login_required
@require_POST
def monthly_billing_run_create(request):
    month = parse_billing_month(request.POST.get("month"))
    run = run_monthly_billing_preflight(month, created_by=request.user)
    messages.success(request, f"Monthly billing preflight created for {month:%B %Y}.")
    return redirect("invoices:monthly_billing_run_detail", pk=run.pk)


@login_required
@require_POST
def monthly_billing_invoice_list_action(request):
    month = parse_billing_month(request.POST.get("month"))
    action = request.POST.get("action")
    run = None

    try:
        if action == "preflight":
            run = run_monthly_billing_preflight(month, created_by=request.user)
            success_message = f"Monthly billing preflight created for {month:%B %Y}."

        else:
            run = (
                MonthlyBillingRun.objects.filter(billing_month=month)
                .order_by("-created_at", "-id")
                .first()
            )
            if not run:
                run = run_monthly_billing_preflight(month, created_by=request.user)

            if action == "generate":
                result = run_monthly_billing_engine(
                    month,
                    created_by=request.user,
                )
                run = result["run"]
                success_message = f"Monthly billing generated for {month:%B %Y}."

            elif action == "send_ready":
                send_monthly_billing_ready(run, created_by=request.user)
                success_message = f"Ready invoices sent for {month:%B %Y}."

            elif action == "retry_failed":
                send_monthly_billing_ready(
                    run, created_by=request.user, retry_failed=True
                )
                success_message = f"Failed invoice sends retried for {month:%B %Y}."

            else:
                raise ValueError("Unknown billing action.")

        if (
            request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or request.META.get("HTTP_X_REQUESTED_WITH") == "XMLHttpRequest"
        ):
            return JsonResponse(
                {
                    "ok": True,
                    "message": success_message,
                    "redirect_url": reverse(
                        "invoices:monthly_billing_run_detail", kwargs={"pk": run.pk}
                    ),
                }
            )

        messages.success(request, success_message)

    except Exception as exc:
        if (
            request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or request.META.get("HTTP_X_REQUESTED_WITH") == "XMLHttpRequest"
        ):
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        messages.error(request, f"Monthly billing action failed: {exc}")

    if run:
        return redirect("invoices:monthly_billing_run_detail", pk=run.pk)
    return redirect("invoices:invoice_list")


@login_required
@require_POST
def monthly_billing_run_action(request, pk):
    run = get_object_or_404(MonthlyBillingRun, pk=pk)
    action = request.POST.get("action")
    try:
        if action == "dry_run":
            summary = run_monthly_billing_dry_run(run.billing_month, run=run)
            messages.success(
                request,
                f"Dry run completed: {summary.get('would_send', 0)} would send, {summary.get('manual_electric', 0)} manual electric.",
            )
        elif action == "run_billing":
            run_monthly_billing_engine(
                run.billing_month,
                created_by=request.user,
            )
            messages.success(request, "Billing run completed through ready validation.")
        elif action == "preflight":
            run_monthly_billing_preflight(run.billing_month, created_by=request.user)
            messages.success(request, "Preflight completed.")
        elif action == "generate_recurring":
            generate_monthly_billing_invoices(run)
            messages.success(request, "Recurring invoices generated.")
        elif action == "generate_electric":
            generate_monthly_billing_electric(run)
            messages.success(request, "Electric billing checks completed.")
        elif action == "prepare_ready":
            prepare_monthly_billing_ready(run)
            messages.success(request, "Ready-to-send review completed.")
        elif action == "generate_pdfs":
            generate_monthly_billing_pdfs(run)
            messages.success(request, "Invoice PDFs generated for ready items.")
        elif action == "send_ready":
            send_monthly_billing_ready(run, created_by=request.user)
            messages.success(request, "Ready invoices sent where possible.")
        elif action == "retry_failed":
            send_monthly_billing_ready(run, created_by=request.user, retry_failed=True)
            messages.success(request, "Failed invoices retried.")
        elif action == "rollback_run":
            blocked = rollback_monthly_billing_run(run, user=request.user)
            if blocked:
                messages.warning(
                    request, f"Rollback finished with {len(blocked)} blocked item(s)."
                )
            else:
                messages.success(request, "Billing run rolled back.")
        else:
            messages.error(request, "Unknown billing action.")
    except Exception as exc:
        messages.error(request, f"Monthly billing action failed: {exc}")
    return redirect("invoices:monthly_billing_run_detail", pk=run.pk)


@login_required
@require_POST
def monthly_billing_run_enqueue(request, pk):
    run = get_object_or_404(MonthlyBillingRun, pk=pk)
    action = request.POST.get("action")
    valid_actions = {choice[0] for choice in BillingProgressJob.ACTION_CHOICES}
    if action not in valid_actions:
        return JsonResponse(
            {"ok": False, "error": "Unknown billing action."}, status=400
        )

    existing = (
        BillingProgressJob.objects.filter(
            billing_run=run,
            action=action,
            status__in=[
                BillingProgressJob.STATUS_QUEUED,
                BillingProgressJob.STATUS_RUNNING,
            ],
        )
        .order_by("-created_at")
        .first()
    )
    if existing:
        return JsonResponse({"ok": True, "job_id": existing.pk})

    progress_job = BillingProgressJob.objects.create(
        billing_run=run,
        action=action,
        created_by=request.user if request.user.is_authenticated else None,
        current_step="Queued",
        message="Queued",
    )
    def _run_locally():
            try:
                from invoices.tasks import run_billing_progress_job

                progress_job.message = "Running locally (Redis disabled)."
                progress_job.save(update_fields=["message", "updated_at"])
                run_billing_progress_job(progress_job.pk)
                return None
            except Exception as local_exc:
                progress_job.status = BillingProgressJob.STATUS_FAILED
                progress_job.error_text = str(local_exc)
                progress_job.completed_at = timezone.now()
                progress_job.save(
                    update_fields=["status", "error_text", "completed_at", "updated_at"]
                )
                return JsonResponse(
                    {"ok": False, "job_id": progress_job.pk, "error": str(local_exc)},
                    status=503,
                )

    if settings.BILLING_RQ_ENABLED:
            try:
                enqueue_billing_job(progress_job)
            except Exception:
                err = _run_locally()
                if err:
                    return err
    else:
            err = _run_locally()
            if err:
                return err
    return JsonResponse({"ok": True, "job_id": progress_job.pk})


@login_required
def monthly_billing_job_status(request, pk):
    progress_job = get_object_or_404(
        BillingProgressJob.objects.select_related("billing_run"), pk=pk
    )
    total = progress_job.total_count or 0
    current = progress_job.current_index or 0
    percent = 0
    if total:
        percent = min(100, int((current / total) * 100))
    elif progress_job.status == BillingProgressJob.STATUS_COMPLETED:
        percent = 100
    return JsonResponse(
        {
            "ok": True,
            "job_id": progress_job.pk,
            "run_id": progress_job.billing_run_id,
            "action": progress_job.action,
            "status": progress_job.status,
            "current_step": progress_job.current_step,
            "tenant": progress_job.current_tenant,
            "property": progress_job.current_property,
            "unit": progress_job.current_unit,
            "current": current,
            "total": total,
            "percent": percent,
            "elapsed_seconds": progress_job.elapsed_seconds,
            "average_seconds": str(progress_job.average_seconds),
            "estimated_remaining_seconds": progress_job.estimated_remaining_seconds,
            "message": progress_job.message,
            "error_text": progress_job.error_text,
            "result": progress_job.result,
        }
    )


@login_required
@require_POST
def monthly_billing_water_update(request, pk):
    item = get_object_or_404(
        MonthlyBillingRunItem.objects.select_related("billing_run"), pk=pk
    )
    try:
        if request.POST.get("not_applicable") == "1":
            resolve_monthly_billing_water(item, not_applicable=True)
            messages.success(request, "Water marked not applicable.")
        else:
            resolve_monthly_billing_water(
                item,
                amount=request.POST.get("water_charge"),
                description=request.POST.get("water_description") or None,
                apply_property=request.POST.get("apply_property") == "1",
            )
            messages.success(request, "Water charge saved.")
        prepare_monthly_billing_ready_for_item(item)
    except Exception as exc:
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        messages.error(request, f"Water update failed: {exc}")
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        item.refresh_from_db()
        water_item = _invoice_water_item(item.invoice)
        run = item.billing_run
        run.refresh_from_db()
        billable_items = run.items.exclude(
            status__in=[
                MonthlyBillingRunItem.STATUS_SKIPPED,
                MonthlyBillingRunItem.STATUS_EXCLUDED,
            ]
        ).select_related("lease")

        water_items = [
            row
            for row in billable_items.select_related("lease", "invoice")
            if row.lease_id and getattr(row.lease, "bill_water_charges", False)
        ]
        return JsonResponse(
            {
                "ok": True,
                "item_id": item.pk,
                "amount": str(getattr(water_item, "amount", item.water_charge or "")),
                "description": getattr(water_item, "description", "")
                or f"Water charges {item.billing_run.billing_month:%b %Y}",
                "water_required": item.water_required,
                "water_resolved": item.water_resolved,
                "status": item.get_status_display(),
                "status_code": item.status,
                "issue_code": item.issue_code,
                "issue_message": item.issue_message,
                "invoice_id": item.invoice_id,
                "invoice_number": item.invoice.invoice_number
                if item.invoice_id
                else "",
                "invoice_url": reverse(
                    "invoices:invoice_detail", kwargs={"pk": item.invoice_id}
                )
                if item.invoice_id
                else "",
                "run_counts": {
                    "water_resolved": sum(
                        1 for row in water_items if _invoice_water_item(row.invoice)
                    ),
                    "water_required": len(water_items),
                    "water_missing": run.water_missing_count,
                    "ready": run.ready_to_send_count,
                    "pending": run.pending_attention_count,
                },
            }
        )
    return redirect("invoices:monthly_billing_run_detail", pk=item.billing_run_id)


@login_required
@require_POST
def monthly_billing_item_action(request, pk):
    item = get_object_or_404(
        MonthlyBillingRunItem.objects.select_related("billing_run"), pk=pk
    )
    action = request.POST.get("action")
    is_ajax = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.META.get("HTTP_X_REQUESTED_WITH") == "XMLHttpRequest"
    )

    try:
        if action == "exclude":
            exclude_monthly_billing_item(
                item, reason=request.POST.get("reason") or "", user=request.user
            )
            success_message = "Item excluded from this billing run."

        elif action == "rollback":
            rollback_monthly_billing_item(item, user=request.user)
            success_message = "Item rolled back."

        elif action == "regenerate_pdf":
            generate_monthly_billing_pdf_for_item(item)
            success_message = "PDF regenerated."

        elif action == "resend_whatsapp":
            send_monthly_billing_item(item, created_by=request.user)
            success_message = "Invoice resend attempted."

        elif action == "approve_electric_reading":
            approve_monthly_billing_electric_reading(item)
            prepare_monthly_billing_ready_for_item(item)
            success_message = "Electric reading accepted."

        else:
            raise ValueError("Unknown item action.")

        if is_ajax:
            item.refresh_from_db()
            run = item.billing_run

            electric_items = run.items.exclude(
                status__in=[
                    MonthlyBillingRunItem.STATUS_SKIPPED,
                    MonthlyBillingRunItem.STATUS_EXCLUDED,
                ]
            ).filter(electric_required=True)

            return JsonResponse(
                {
                    "ok": True,
                    "message": success_message,
                    "item_id": item.pk,
                    "status": item.status,
                    "status_label": item.get_status_display(),
                    "electric_ready": item.electric_ready,
                    "issue_code": item.issue_code,
                    "issue_message": item.issue_message,
                    "run_counts": {
                        "electric_ready": electric_items.filter(
                            electric_ready=True
                        ).count(),
                        "electric_total": electric_items.count(),
                        "electric_pending": electric_items.filter(
                            electric_ready=False
                        ).count(),
                        "ready": run.items.filter(
                            status=MonthlyBillingRunItem.STATUS_READY
                        ).count(),
                        "pending": run.items.filter(
                            status=MonthlyBillingRunItem.STATUS_PENDING
                        ).count(),
                    },
                }
            )

        messages.success(request, success_message)

    except Exception as exc:
        if is_ajax:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        messages.error(request, f"Item action failed: {exc}")

    return redirect("invoices:monthly_billing_run_detail", pk=item.billing_run_id)


@login_required
def monthly_billing_dry_run_json(request, pk):
    run = get_object_or_404(MonthlyBillingRun, pk=pk)
    return JsonResponse(run_monthly_billing_dry_run(run.billing_month, run=run))


@login_required
def monthly_billing_run_export(request, pk):
    import csv

    run = get_object_or_404(MonthlyBillingRun, pk=pk)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="monthly_billing_{run.billing_month:%Y_%m}.csv"'
    )
    writer = csv.writer(response)
    writer.writerow(
        [
            "billing_month",
            "tenant",
            "property",
            "unit",
            "invoice",
            "status",
            "issue_code",
            "issue_message",
            "recurring_found",
            "electric_required",
            "electric_ready",
            "latest_meter_reading_date",
            "water_required",
            "water_charge",
            "water_resolved",
            "invoice_total",
            "whatsapp_status",
            "whatsapp_message_id",
            "sent_at",
            "error_text",
        ]
    )
    for item in run.items.select_related("tenant", "property", "unit", "invoice"):
        writer.writerow(
            [
                run.billing_month,
                _tenant_name(item.tenant) if item.tenant else "",
                getattr(item.property, "property_name", ""),
                getattr(item.unit, "unit_number", ""),
                getattr(item.invoice, "invoice_number", ""),
                item.get_status_display(),
                item.issue_code,
                item.issue_message,
                item.recurring_invoice_found,
                item.electric_required,
                item.electric_ready,
                item.latest_meter_reading_date,
                item.water_required,
                item.water_charge,
                item.water_resolved,
                item.invoice_total,
                item.whatsapp_status,
                item.whatsapp_message_id,
                item.sent_at,
                item.error_text,
            ]
        )
    return response
