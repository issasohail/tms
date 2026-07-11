TMS current-project affected-files patch
=======================================

This patch was built from the uploaded current project tms3(2).zip. It contains only changed/new files.

IMPORTANT BACKUP
1. Commit or copy your current project before copying these files.
2. Extract this patch into a separate folder.
3. Compare files, then copy them into the same relative paths in your project.

IMPLEMENTED
- Public registration approved compact desktop/tablet/mobile layout.
- Desktop: Vehicle, Proposer, Seconder, Witness 1, Witness 2 in five cards.
- Tablet: compact two-card arrangement with a wide vehicle card.
- Mobile: three vehicle fields per row; compact 2-3 person fields per row.
- Family member cards include photo, CNIC front and CNIC back uploads.
- Phone labels changed to Phone 1, Phone 2, Phone 3.
- Employer phone and employer address included.
- Personal information is compact; # of Family label, relation dropdown, CNIC in sixth first-row cell, one-line wide notes.
- Submitted confirmation uses the pending submitted photo before the live Tenant photo.
- Authorized Occupants includes the primary tenant and family members, displayed three persons per PDF row.
- Curly and square placeholder syntax remain supported.
- Lease/Renewal linked Tenant witnesses are now the only runtime witness source.
- Migration attempts to backfill linked witnesses from legacy CNIC before removing legacy text fields.
- Agreement clause page has Proposer, Seconder, Witness 1 and Witness 2 Select2 controls for the selected lease/history.
- Agreement clause page now has one Download PDF action, linked to the combined package generator, plus Inspection.
- Lease detail Tenant & Police overview column replaced by current Agreement Parties; duplicate bottom party card removed.
- Signature-page wording/options can be edited from Settings > Agreement Placeholders > Edit Agreement Signature Page.
- Combined PDF order remains Agreement, Inspection, Police Report, Signature Page.

MIGRATIONS
- tenants/migrations/0020_tenant_employer_address.py
- leases/migrations/0071_remove_legacy_witness_fields.py
- leases/migrations/0072_agreement_signature_template.py

INSTALL
PowerShell:
  cd E:\tenant_management_system
  .\.venv\Scripts\Activate.ps1
  python -m pip install pypdf
  python manage.py makemigrations --check --dry-run
  python manage.py migrate
  python manage.py check
  python manage.py test tenants leases

No standalone static file was changed. collectstatic is not required for local testing, but run your normal production collectstatic command during deployment.

MANUAL CHECKS
1. Open an existing renewal and select Witness 1/2 Tenant records.
2. Open Edit Clauses for that renewal; confirm the same witnesses are selected.
3. Download PDF and confirm the merged package order.
4. Confirm Authorized Occupants renders primary tenant plus family, three cards per row.
5. Open lease detail and confirm Agreement Parties replace Tenant & Police.
6. Test public registration at desktop, tablet, 360px and smaller widths.
7. Submit a new photo and confirm it appears on the submitted page.
8. Add family member documents and confirm pending files are saved.
9. Open /tms/leases/settings/agreement-signature-template/ and edit signature wording.

ROLLBACK WARNING
Migration 0071 removes legacy witness text columns after backfilling CNIC matches. Back up the database before migration. If a legacy witness CNIC does not match an existing Tenant, it cannot be automatically linked.
