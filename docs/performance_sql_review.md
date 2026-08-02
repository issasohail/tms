# TMS SQL Performance Review

Reviewed on 2026-05-24.

## Debug Toolbar Usage

1. Install dependencies from `requirements.txt`.
2. Run migrations locally so `GlobalSettings.enable_debug_toolbar` exists.
3. Start the local development server with `DEBUG=True`.
4. Sign in as a staff or superuser account.
5. Open **System Settings** and check **Enable Django Debug Toolbar (local development only)**.
6. Open high-value pages and inspect the Debug Toolbar SQL panel.
7. Look for duplicate, similar, and repeated per-row queries.

The toolbar is fail-closed and only shows when `DEBUG=True`, the request host is local/private, the user is staff or superuser, and the settings-table toggle is enabled. It is explicitly blocked for `kirayas.com` and `www.kirayas.com`.

## Views Reviewed

- `dashboard.views.dashboard`
- `core.views.SettingsView`
- `core.context_processors.global_settings`
- `tenants.views.TenantListView`
- `tenants.views.TenantDetailView`
- `tenants.views.TenantLedgerView`
- `properties.views.PropertyListView`
- `properties.views.PropertyDetailView`
- `properties.views.UnitListView`
- `properties.views.UnitDetailView`
- `properties.views.unit_media_page`
- `invoices.views.InvoiceListView`
- `invoices.views.InvoiceDetailView`
- `invoices.views._invoice_pdf_context`
- `invoices.views.recurring_list`
- `invoices.views.RecurringChargeListView`
- `payments.views.PaymentListView`
- `payments.views.PaymentDetailView`
- `payments.views.PaymentCreateView`
- `payments.views.payment_pdf_view`
- `payments.views.send_receipt`
- `payments.views.send_payment_email`
- `maintenance.views`
- `smart_meter.views`

## Problems Found

- `dashboard.views.dashboard` loaded active tenants, then queried each tenant's active lease and last payment inside a loop. It also called `Lease.get_balance`, which performs invoice and payment aggregates per lease.
- `core.context_processors.global_settings` queried `GlobalSettings.get_solo()` on every template-rendered request.
- `properties.views.UnitListView` and unit detail/media views accessed `unit.property` in templates without consistently selecting the property relation.
- `payments.views.PaymentDetailView` and PDF/email helpers loaded `Payment` without preloading `lease -> tenant`, `lease -> unit -> property`, and `payment_method`.
- `invoices.views.InvoiceDetailView` loaded invoice items and then ran a second aggregate query for the total. The template also needs `lease -> tenant`, `lease -> unit -> property`, and item categories.
- Templates with likely N+1 exposure include tenant list/PDF rows (`tenant.current_lease`, `lease.unit.property`, `lease.get_balance`, `lease.security_due`) and shared action button components (`record.lease.tenant`, `record.lease.unit.property`, `record.items.all`).

## Fixes Applied

- `dashboard.views.dashboard`
  - Current pattern: loop over active tenants, call `tenant.leases.filter(...).first()`, `Payment.objects.filter(...).first()`, and `active_lease.get_balance`.
  - Optimized pattern: load active leases once with `select_related("tenant", "unit__property")`, then build invoice and payment aggregate maps grouped by `lease_id`.
  - Result: tenant balance section uses fixed aggregate queries instead of per-tenant lookups.

- `core.context_processors.global_settings`
  - Current pattern: `GlobalSettings.get_solo()` on every rendered request.
  - Optimized pattern: cache the singleton settings object for 60 seconds and clear it after settings save.

- `properties.views.UnitListView`
  - Current queryset: `super().get_queryset()`.
  - Optimized queryset: `super().get_queryset().select_related("property")`.

- `properties.views.UnitDetailView`, `unit_detail`, `unit_media_page`, share-token unit lookup, and `unit_media_share_link`
  - Current lookup: `Unit.objects` by primary key.
  - Optimized lookup: `Unit.objects.select_related("property")`.

- `payments.views.PaymentDetailView`, `send_receipt`, `payment_pdf_view1`, `payment_pdf_view2`, `payment_pdf_view`, and `send_payment_email`
  - Current lookup: plain `Payment` lookup.
  - Optimized lookup: `select_related("lease__tenant", "lease__unit__property", "payment_method")`.
  - Detail view also prefetches `security_deposit_movements`.

- `invoices.views.InvoiceDetailView`
  - Current queryset: plain `Invoice`; context queried `inv.items.select_related("category")` and then aggregated the same items.
  - Optimized queryset: `select_related("lease__tenant", "lease__unit__property")` plus `Prefetch("items", queryset=InvoiceItem.objects.select_related("category"))`.
  - Total is computed from the loaded item list to avoid the extra aggregate query.

## Still Needs Manual Profiling

- `tenants.views.TenantListView`: the active list already prefetches leases, units, and properties, but template calls to `lease.get_balance` and `lease.security_due` can still perform aggregates per row.
- `tenants.views.TenantDetailView`: repeated invoice/payment aggregates should be profiled against real data before consolidation.
- `leases.views`: large file with many detail/history/renewal paths; focus on detail, history, ledger, agreement, and security-deposit pages.
- `invoices.views` recurring charge and backfill endpoints: duplicate `.exists()` checks inside nested loops are business-sensitive and should be optimized with preloaded sets after confirming expected duplicate rules.
- `payments.views.PaymentListView`: already uses `select_related` and grouped totals; verify table/render/export behavior with Debug Toolbar.
- `maintenance.views`: likely benefits from `select_related("tenant", "building", "unit", "lease", "assigned_to", "created_by", "updated_by")` and `prefetch_related("media", "status_logs")`.
- `smart_meter.views`: dashboards and reading lists should be profiled with real reading volumes, especially meter/unit/property and latest-reading access.

## Query Count Notes

Live before/after query counts were not captured because the local MySQL service at `localhost:6604` refused connections during migration history checks. Use the Debug Toolbar SQL panel locally after starting MySQL to capture exact counts.

Expected reductions:

- Dashboard tenant balances: from roughly `2 + 3N` queries for `N` active tenants to fixed grouped queries for active leases, invoice totals, and payment totals.
- Invoice detail: removes one extra item aggregate query and preloads lease/tenant/unit/property and item categories.
- Payment detail/PDF/email: removes related-object queries for tenant, unit, property, and payment method during rendering.
- Settings/global context: removes one `GlobalSettings` query per rendered request for most requests within the 60-second cache window.
