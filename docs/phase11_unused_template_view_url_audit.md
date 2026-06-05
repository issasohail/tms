# Phase 11 - Unused Template / View / URL Audit

Date: 2026-06-06

Scope: static scan of Python/template references plus Django URL resolver inspection. No files were deleted.

## Summary

- Templates scanned: 350
- Templates with no direct `render`, `render_to_string`, `template_name`, `{% extends %}`, or `{% include %}` reference: 151
- Backup / old / copy / dont-use style files found: 146
- Template URL names checked: 317 unique names
- Missing template URL names: 18
- Runtime URL patterns resolved: 1166
- Duplicate runtime route strings: 69
- Duplicate runtime URL names: 65
- Unreferenced view candidates by static symbol scan: 4

## High Confidence Safe To Delete

These are orphaned old lead templates. They are not referenced by current views/includes/extends, and all lead URL names used inside them are missing.

- `tenants/templates/tenants/lead_list.html`
- `tenants/templates/tenants/lead_detail.html`
- `tenants/templates/tenants/lead_form.html`
- `tenants/templates/tenants/lead_vacancy_whatsapp.html`

Why: `PotentialTenantLead` was created in `tenants/migrations/0008_potentialtenantlead.py`, old lead data was migrated into `Tenant` in `tenants/migrations/0011_tenantinteresttype_tenant_budget_tenant_family_size_and_more.py`, and `PotentialTenantLead` plus temporary lead fields were removed in `tenants/migrations/0012_remove_tenant_budget_remove_tenant_family_size_and_more.py`. Current lead functionality uses `Tenant.interested_in`, `TenantInterestType`, `properties.views.unit_vacant_notice_leads`, and `tenants.views.tenant_lead_inline_update`.

## Keep Because Used Indirectly / Current Feature

- `tenants/templates/tenants/tenant_form.html`
  - Used by `TenantCreateView` and `TenantUpdateView`.
- `tenants/templates/tenants/tenant_list.html`
  - Used by `TenantListView`.
- `tenants/templates/tenants/tenant_detail.html`
  - Used by `TenantDetailView`.
- `properties/templates/properties/includes/vacant_notice_modal.html`
- `properties/templates/properties/includes/vacant_notice_scripts.html`
- `properties/templates/properties/unit_vacancy_whatsapp.html`
  - Part of current vacant-unit WhatsApp/lead workflow.
- `properties/templates/properties/unit_actions.html`
  - References `properties:unit_vacant_notice_leads`.
- `tenants/templates/tenants/_whatsapp_phone_link.html`
  - Include-style partial; should be kept unless manually confirmed unused in rendered pages.

## Broken / Missing Routes That Should Be Restored Or Removed From Old Templates

These URL names are referenced from templates but do not exist in the current resolver:

- `tenants:lead_create`
- `tenants:lead_detail`
- `tenants:lead_list`
- `tenants:lead_update`
- `tenants:lead_vacancy_whatsapp`
- `tenants:ledger_pdf`
- `dashboard:dashboard`
- `generate_bill`
- `invoices:detail`
- `leases:create`
- `notifications:notification_list`
- `payments:generate_monthly_invoices`
- `payments:invoice_create`
- `payments:invoice_delete`
- `payments:invoice_update`
- `reports:FinancialReportListView`
- `reports:report_detail`
- `reports:report_update`

Classification: the `tenants:lead_*` names should not be restored unless a lead module is intentionally rebuilt. They live only in orphan/old templates except `tenant_list-v2.html`, which is itself an old variant.

## Duplicated / Unreachable URL Patterns

### Must Fix

- `tenants/create/`
  - First route: `TenantCreateView`, name `tenant_create`
  - Second route: `NotificationCreateView`, name `create`
  - Classification: duplicated route and the notification create route is unreachable under `tenants/create/`.
  - Recommendation: remove the notification imports/routes from `tenants/urls.py`; notification routes already exist in `notifications/urls.py`.

### Strong Cleanup Candidates

- `accounts/` routes are included more than once in `tms/urls.py`, plus `django.contrib.auth.urls` is included under the same prefix.
- `leases/get_units/` maps to both `GetUnitsView` and `get_units`.
- `leases/<int:pk>/email/` maps to both `lease_email` and `SendAgreementEmailView`.
- `leases/lease/<int:lease_id>/photos/`, `photos/grid/`, and `photo/<int:photo_id>/view/` are duplicated.
- `payments/allocations/<int:pk>/delete/` is duplicated.
- `payments/allocations/<int:pk>/edit/` maps to both `AllocationUpdateView` and `AllocationEditView`.
- `payments/api/allocation/<int:pk>/whatsapp/` is duplicated.
- `smart-meter/unknown/` is duplicated.
- `smart-meter/invoice/electric/preview/<int:lease_id>/<int:meter_id>/` and commit equivalents are duplicated.

Classification: broken / unreachable route cleanup, not safe-delete template cleanup.

## Unreferenced View Candidates

Static symbol scan found these top-level view definitions with no other Python references:

- `utilities.views.utility_list_view`
- `reports.views.ReportCreateView`
- `leases.views.lease_applied_amount`
- `expenses.views.ExpenseDistributionCreateView`

Classification: investigate before deletion. These may be vestigial, but view deletion should wait for URL/template confirmation and app-owner intent.

## Backup / Old File Cleanup Candidates

Classification: move to archive or delete after approval. These match `copy`, `.bak`, `-old`, `-working`, `-dontuse`, `-not in use`, `-v1`, `-v2`, or `-v3` style names.

High-confidence template backup groups:

- `invoices/templates/invoices/invoice_list copy*.html`
- `invoices/templates/invoices/invoice_detail copy*.html`
- `invoices/templates/invoices/invoice_form-dontuse.html`
- `payments/templates/payments/payment_form copy*.html`
- `payments/templates/payments/payment_list-dontuse.html`
- `payments/templates/payments/payment_pdf - reportlab-working.html`
- `payments/templates/payments/cash_ledger copy*.html`
- `leases/templates/leases/lease_list copy*.html`
- `leases/templates/leases/lease_list-v2.html`
- `leases/templates/leases/lease_list-working but old.html`
- `leases/templates/leases/lease_detail copy.html`
- `leases/templates/leases/lease_detail-v1-05262026.html`
- `leases/templates/leases/lease_detail-v2.html`
- `leases/templates/leases/lease_agreement-not in use.html`
- `properties/templates/properties/unit_list-v1.html`
- `properties/templates/properties/unit_list-v2.html`
- `properties/templates/properties/unit_list-v3.html`
- `properties/templates/properties/unit_list-working.html`
- `properties/templates/properties/unit_form-old.html`
- `properties/templates/properties/unit_form-v1.html`
- `tenants/templates/tenants/tenant_list copy.html`
- `tenants/templates/tenants/tenant_list-v2.html`
- `tenants/templates/tenants/tenant_list-noIDcard.html`
- `tenants/templates/tenants/tenant_detail copy*.html`
- `tenants/templates/tenants/tenant_detail-v2.html`
- `tenants/templates/tenants/tenant_detail-v3.html`
- `tenants/templates/tenants/tenant_form copy.html`
- `tenants/templates/tenants/tenant_form-v1.html`
- `smart_meter/templates/smart_meter/* copy*.html`
- `expenses/templates/expenses/expense_list copy*.html`
- `expenses/templates/expenses/expense_form copy*.html`
- `reports/templates/reports/* copy.html`
- `templates/partials/navbar copy*.html`
- `templates/base-v1.html`
- `templates/dashboard copy.html`
- `templates/dashboard-rename.html`

High-confidence Python backup groups:

- `invoices/views copy*.py`
- `invoices/views-dont.py`
- `payments/views copy.py`
- `payments/views-not working.py`
- `payments/views/allocations copy*.py`
- `payments/views/payments copy.py`
- `payments/forms copy.py`
- `payments/tables copy.py`
- `payments/tables.-working.py`
- `tenants/views-working.py`
- `tenants/tables-working.py`
- `tenants/tables copy*.py`
- `leases/admin copy.py`
- `leases/forms copy.py`
- `properties/pdf_export-working-but dont use.py`
- `smart_meter/views copy.py`
- `smart_meter/views_dashboard copy*.py`
- `smart_meter/views_dashboard.py- not working`
- `smart_meter/utils-not-in-use.py`
- `smart_meter/vendor/prepaid-v1.py`
- `smart_meter/vendor/prepaid copy.py`
- `utils/pdf_export-working.py`
- `utils/pdf_export copy.py`

## Recommended Phase 11 Cleanup Order

1. Delete the four orphan lead templates.
2. Remove the unreachable notification routes/imports from `tenants/urls.py`.
3. Run `python manage.py check`.
4. Commit.
5. Archive/delete backup old files in batches by app, with one commit per app.
6. Fix duplicated URL routes in focused app-level commits.

No migrations are required for the lead template cleanup.
