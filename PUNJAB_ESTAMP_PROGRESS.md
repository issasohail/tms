# Punjab e-Stamp Integration Progress

## 2026-09-26 — Corrected Phase 0 Repository Recon

### Scope and authoritative correction

This repository inspection supersedes the original prompt's assumption that a Punjab-specific e-Stamp workflow already existed. No implementation code or migrations were written in this phase.

The following do **not** exist in this snapshot and will not be searched for or treated as reusable code:

- `LeaseEStampWorkflow`
- `leases/services/punjab_estamp.py`
- `leases/views_estamp_portal.py`
- Punjab challan, PSID, OTP, or stamp-retrieval workflow
- Punjab portal browser helper/userscript
- `leases/0102`

The existing reusable feature is the generic/manual e-Stamp PDF flow described below. The Punjab integration will be a new feature in a new `punjab_estamp` app, and its final PDF plus PIN handoff must reuse this generic flow.

`AGENTS.md` exists at the repository root and applies to all work.

### Existing generic e-Stamp implementation to reuse

#### Core PDF normalization

- `leases/services/estamp.py`
  - `ESTAMP_CATEGORY = "estamp_paper"`
  - `normalize_estamp_pdf(upload, password="")` reads the PDF, decrypts it when a password is supplied, rejects a missing or incorrect password, rejects empty PDFs, rewrites all pages into a new unlocked PDF, and returns a Django `ContentFile`.
  - The supplied password is not persisted.
  - `latest_estamp(lease)`, `estamp_status(...)`, and `authorize_estamp(...)` provide the current generic agreement/export selection and age-check behavior.

#### Manual web upload

- `leases/views_lease_files.py`
  - `lease_file_upload(request, lease_id)` is the current manual upload view.
  - For category `estamp_paper`, it requires PDF format, calls `normalize_estamp_pdf(upload, request.POST.get("estamp_password") or "")`, assigns the standard filename, and saves a `LeaseDocument`.
  - `_estamp_filename(lease)` generates `<Property>-<Unit>_StampPaper_<MMDDYYYY>.pdf`.
  - The current view always assigns `lease_history=None`, even when redirecting from a selected agreement history. The Punjab handoff must preserve its exact `LeaseRenewal`; shared document-creation code will therefore accept an explicit history while retaining existing manual behavior.
- `leases/templates/leases/edit_clause.html`
  - Contains the existing manual e-Stamp upload modal, optional PDF password field, status/age display, and agreement export controls.
- `leases/templates/leases/lease_detail.html`
  - Contains AJAX upload handling and a password prompt for protected e-Stamp PDFs.

#### WhatsApp/manual approval path

- `whatsapp/services/estamp_processor.py`
  - `inspect_estamp_pdf(...)` validates and inspects received PDFs.
  - `unlock_estamp_pdf(file_field, password)` delegates decryption to `leases.services.estamp.normalize_estamp_pdf()` and replaces the pending file with the unlocked rewritten PDF.
- `whatsapp/admin.py`
  - `_attach_pending_media(...)` handles an approved `TARGET_LEASE_ESTAMP`, calls `normalize_estamp_pdf()`, uses `_estamp_filename()`, and creates a `LeaseDocument(category="estamp_paper")`.

#### Planned final handoff reuse

Phase 6 will not introduce another PDF processor. The existing normalization and document-creation statements will be factored into one shared leases service function used by:

1. `lease_file_upload()` for the existing manual web flow;
2. the existing WhatsApp approval handoff; and
3. the new Punjab workflow's downloaded PDF plus session-only PIN handoff.

The shared function will normalize/decrypt, apply the existing filename convention, and create the same `LeaseDocument` category. It will additionally accept the exact `LeaseRenewal` and behave idempotently for a Punjab workflow.

### Exact current Property model fields relevant to this feature

`properties.models.Property` currently contains:

- Identity/basic fields: `property_name`, `type`, `property_type`, `total_units`, `description`, `created_at`, `updated_at`.
- Legacy owner fields: `owner_prefix`, `owner_name`, `owner_father_name`, `relation` (text), `owner_phone`, `owner_address`, `owner_cnic`, `owner_photo`.
- Legacy caretaker fields: `caretaker_prefix`, `caretaker_name`, `caretaker_father_name`, `caretaker_relation` (text), `caretaker_address`, `caretaker_cnic`, `caretaker_phone`.
- Address fields: `property_address1`, `property_address2`, `property_city`, `property_state`, `property_zipcode`.
- Police-verification address fields: `house_no`, `street_no`, `colony`, `road`, `covered_area_type`, `police_station`, `police_division`, `police_circle`, and `zila`.
- `zila` is currently `CharField(max_length=120, blank=True, default="")`.
- There is currently no `tehsil`, district FK, owner Tenant FK, or caretaker Tenant FK.
- Other current Property configuration includes `bank_account_details`, `welcome_bank_account_mode`, and `electricity_unit_rate`.

Repository note: the source currently declares `owner_phone` twice and `caretaker_prefix` twice with equivalent definitions. This pre-existing duplication is outside this phase and must not be refactored incidentally.

### Exact current Tenant/person model fields relevant to this feature

`tenants.models.Tenant` is the existing reusable person identity model. It currently contains:

- Name/relationship: `prefix`, `first_name`, `relation`, `last_name`.
- `relation` is currently `CharField(max_length=10, null=True, blank=True, default="S/O.")`.
- Contact/identity: `email`, `phone`, `phone2`, `phone3`, `cnic`, `cnic_digits`.
- Address/location: `address`, `temporary_address`, `permanent_address`, `temporary_address_urdu`, `permanent_address_urdu`, `working_address`, `city`, `province`, `country`, `nationality`.
- Employment/reference: `occupation`, `monthly_income_bracket`, `employer_name`, `employer_phone`, `employer_address`, `reference_name_1`, `reference_phone_1`, `reference_relation_1`, `reference_name_2`, `reference_phone_2`, `reference_relation_2`.
- Personal/family: `gender`, `date_of_birth`, `cnic_issue_date`, `cnic_expiry_date`, `emergency_contact_name`, `emergency_contact_phone`, `emergency_contact_relation`, `number_of_family_member`, `family_member_adults`, `family_member_children`, `nadra_family_no`.
- Lifecycle/media/police verification: `created_at`, `updated_at`, `is_active`, `interested_in`, `notes`, `photo`, `photo_crop`, `cnic_front`, `cnic_front_crop`, `cnic_back`, `cnic_back_crop`, `police_verification_status`, `police_verification_date`, `police_verification_document`, `police_verification_remarks`, `police_verification_follow_up_date`, `police_verified_by`.
- `get_full_name_agreement()` currently renders `first_name`, the text `relation`, and `last_name` directly. It must be updated when `relation` becomes a Punjab relation FK so agreement text keeps its current meaning.

Decision confirmed by the user: `Tenant.relation` will be converted to the database-backed Punjab relation list. Property owner/caretaker Punjab relations will come from their linked Tenant records; no competing Property Punjab-relation FK will be added. Existing Property relation text fields remain as migration-period fallback data.

### Exact Lease and renewal/history relationship

- `leases.models.Lease` has `tenant = ForeignKey(Tenant, related_name="leases")` and `unit = ForeignKey(Unit, related_name="leases")`, plus the current lease terms and dates.
- `leases.models_renewal.LeaseRenewal` has `lease = ForeignKey("leases.Lease", related_name="renewals", on_delete=CASCADE)`.
- `LeaseRenewal` has `renewal_number`, `is_original`, dates, financial/term snapshot fields, generated document fields, and audit fields.
- Its database uniqueness is currently `unique_together = [("lease", "renewal_number")]`.
- `leases.services.lease_history.ensure_original_history()` ensures every lease used by the agreement editor has an original history row with `renewal_number=1` and `is_original=True`.
- `leases.views.edit_clauses()` calls that helper when the original history is absent and then operates against the selected `LeaseRenewal` history.

#### Workflow uniqueness decision

The project uses MySQL, where a conditional/partial unique constraint is not a portable enforcement mechanism. Rather than retaining a nullable `lease_history` and risking duplicate original workflows, `LeaseEStampWorkflow.lease_history` will be non-null and one-to-one with `LeaseRenewal`.

The existing `is_original=True` renewal row represents the original lease, so the same identity scheme covers both original agreements and later renewals. A second workflow for the same history is rejected by a real database unique constraint. The workflow will also carry/validate its lease reference against `lease_history.lease` for direct lookup and consistency.

### Exact LeaseDocument models

`leases.models.LeaseDocumentCategory` contains:

- `code` (unique slug)
- `name`
- `is_active`
- `sort_order`

The seeded category code is `estamp_paper`.

`leases.models.LeaseDocument` contains:

- `lease = ForeignKey("leases.Lease", related_name="documents")`
- `lease_history = ForeignKey("leases.LeaseRenewal", related_name="documents", null=True, blank=True, on_delete=SET_NULL)`
- `file`
- `original_filename`
- `display_name`
- `category`, whose choices include `("estamp_paper", "E-Stamp Paper")`
- `description`
- `uploaded_by`
- `uploaded_at`
- `is_active`
- `share_token`
- `share_expires_at`

No second document model will be created. The workflow will link to the resulting existing `LeaseDocument`.

### Migration state and dependency plan

Current migration tips confirmed from the repository:

- `leases/0101_alter_lease_electric_unit_rate.py`
- `properties/0034_unit_iesco_bill_active.py`
- `tenants/0030_temporary_registration_upload.py`

Planned graph:

1. `punjab_estamp/0001_configuration_models`
   - Create District, Tehsil, Relation, and Purpose configuration tables.
   - No dependency on leases/properties/tenants.
2. `punjab_estamp/0002_lease_estamp_workflow`
   - Create `LeaseEStampWorkflow`.
   - Depends on `punjab_estamp/0001` and `leases/0101`.
3. `punjab_estamp/0003_seed_verified_configuration`
   - Seed the verified 41 Districts, 7 Rawalpindi Tehsils, 9 Relations, and default Purpose.
   - Depends on `punjab_estamp/0002`.
4. `tenants/0031_add_punjab_relation_fk`
   - Add a temporary nullable Punjab relation FK.
   - Depends on `tenants/0030` and `punjab_estamp/0001`.
5. `tenants/0032_backfill_and_swap_relation`
   - Normalize known legacy relation text, populate the FK, retain/report unknowns for review, and only then perform the field swap approved at the phase checkpoint.
   - Depends on `tenants/0031` and seeded `punjab_estamp/0003`.
6. `properties/0035_add_zila_fk_and_tehsil`
   - Add nullable `zila_fk` and `tehsil` FKs while retaining textual `zila`.
   - Depends on `properties/0034` and `punjab_estamp/0001`.
7. `properties/0036_backfill_zila_and_tehsil`
   - Case-insensitive/trimmed district mapping and evidence-based Rawalpindi tehsil mapping; unknowns remain null and are reported.
   - Depends on `properties/0035` and seeded `punjab_estamp/0003`.
8. `properties/0037_swap_zila_fields`
   - Remove old text `zila` and rename `zila_fk` to `zila` only after mapping review.
   - Depends on `properties/0036`.
9. Later Phase 4 Property migrations will add and safely backfill `owner_tenant` and `caretaker_tenant` using exact normalized CNIC matches only.

No `leases/0102` schema migration is presently required. The new workflow schema belongs to `punjab_estamp`, and reuse of the PDF path is a service/view refactor rather than a leases schema change. The next leases-owned migration remains `0102`; it will only be created if Phase 1 inspection identifies a genuine leases-owned schema change. No empty migration will be created solely to consume the number.

### Files changed in this corrected recon checkpoint

- Created `PUNJAB_ESTAMP_PROGRESS.md`.

No existing source file was changed, so no timestamped source backup was required. No migrations were created or applied. No tests were run because this checkpoint changed documentation only. `.env`, settings, deployment configuration, nginx, systemd, secrets, and production data were not modified.

### Next checkpoint

Stop here. Phase 1 code must not start until the user approves this corrected recon record and migration/uniqueness design.

## 2026-09-26 — Phase 1: Core Models, Staging Schema, and Uniqueness

### Work completed

Created the new `punjab_estamp` Django app and registered it in the normal and local SQLite test settings.

Added these database-backed configuration models:

- `PunjabEStampDistrict`: `name`, unique `portal_value`, `active`, `sort_order`, and `last_synced_at`.
- `PunjabEStampTehsil`: District FK, `name`, indexed `portal_value`, `active`, and `sort_order`; unique within its District by both name and portal value.
- `PunjabEStampRelation`: `name`, unique `portal_value`, `active`, and `sort_order`.
- `PunjabEStampPurpose`: `name`, unique `portal_value`, `denomination`, `default_continuation_sheets`, `active`, `is_default_for_lease`, and `sort_order`.

Added `LeaseEStampWorkflow` with:

- Required Lease FK.
- Required one-to-one `lease_history` link to `LeaseRenewal`.
- Optional current Purpose reference and optional one-to-one resulting `LeaseDocument` reference.
- Workflow states: Ready, Challan Generated, Paid, Stamp Issued, Uploaded to TMS, Invalid, and Failed.
- Separate immutable applicant/property/portal JSON snapshot fields for Phase 5.
- Indexed Challan, PSID, and Stamp identifiers.
- Challan replacement audit history and replacement count.
- State timestamps, last portal error/check fields, user audit fields, and created/updated timestamps.
- Model validation that Lease, LeaseRenewal, and LeaseDocument references are consistent.
- No OTP or PIN/password field.

Registered all new models in Django admin for review and configuration.

Added the first safe Property conversion stage:

- Kept the existing textual `Property.zila` untouched.
- Added nullable `Property.zila_fk -> PunjabEStampDistrict` labelled `Zila / District`.
- Added nullable `Property.tehsil -> PunjabEStampTehsil`.
- Both use `PROTECT`, preventing active configuration rows from being deleted while referenced.
- No competing `Property.district` field was introduced.

No seed data, form/UI changes, Tenant relation changes, owner/caretaker links, portal integration, or PDF-processing changes were included in this phase.

### Uniqueness decision

The original agreement is already represented by a real `LeaseRenewal` row with `renewal_number=1` and `is_original=True`. `LeaseEStampWorkflow.lease_history` is therefore required and implemented as `OneToOneField`.

This is the sentinel/original-marker-row approach from the specification. It avoids nullable uniqueness completely and is enforceable on the project's MySQL database. The constraint guarantees one Punjab workflow for the original history and one for each later renewal. A later renewal has a different LeaseRenewal row and can have its own workflow.

The workflow also stores the Lease FK for direct queries. Model validation rejects a LeaseRenewal belonging to another Lease. Workflow creation services in Phase 5 will call this validation inside a transaction.

### Migrations created

- `punjab_estamp/migrations/0001_configuration_models.py`
  - Creates District, Tehsil, Relation, and Purpose configuration tables.
- `punjab_estamp/migrations/0002_lease_estamp_workflow.py`
  - Creates `LeaseEStampWorkflow`.
  - Depends on `punjab_estamp/0001`, `leases/0101`, and the swappable user model.
- `properties/migrations/0035_property_punjab_zila_tehsil_staging.py`
  - Adds nullable `zila_fk` and `tehsil` while retaining textual `zila`.
  - Depends on `properties/0034` and `punjab_estamp/0001`.

No `leases/0102` migration was needed because no leases-owned schema changed. No `tenants/0031` migration was created yet because Tenant relation conversion belongs to the later owner/caretaker phase and requires the Phase 2 verified Relation seed.

The migrations were written and checked but were **not applied**.

### Planned Phase 2 and Property matching logic

Phase 2 will create `punjab_estamp/0003_seed_verified_configuration.py` with the supplied verified District, Rawalpindi Tehsil, Relation, and Purpose mappings.

After seed verification, the Property data migration will:

1. Normalize old `zila` text with surrounding whitespace removed and case-insensitive comparison.
2. Match only an exact normalized District name; it will not use partial/fuzzy matching.
3. Leave `zila_fk` null for unknown or ambiguous values and report those Property IDs/values.
4. Set Rawalpindi Tehsil only when existing zila, city, or address evidence confidently identifies Rawalpindi and the District mapping is Rawalpindi.
5. Never map every Punjab property to Rawalpindi by default.
6. Preserve textual `zila` until the mapping result is reviewed and the later destructive swap is separately approved.

### Tests and checks

- `python manage.py check` — passed with no issues.
- `python manage.py makemigrations --check --dry-run` — passed; no model/migration drift detected.
- `python manage.py test punjab_estamp.tests.test_workflow_constraints --settings=tms.settings_test_sqlite --verbosity 2` — passed all 3 tests:
  - A workflow cannot be created with null `lease_history`.
  - The same original LeaseRenewal cannot have two workflows.
  - A later renewal can have its own workflow.
- `git diff --check` — passed.

The focused tests used the approved isolated in-memory SQLite test configuration because the repository's local test settings intentionally bypass the legacy production migration chain. Production migrations were not run.

### Files changed

Existing files modified:

- `properties/models.py`
- `tms/settings.py`
- `tms/settings_test_sqlite.py`
- `PUNJAB_ESTAMP_PROGRESS.md`

New files:

- `punjab_estamp/__init__.py`
- `punjab_estamp/apps.py`
- `punjab_estamp/models.py`
- `punjab_estamp/admin.py`
- `punjab_estamp/migrations/__init__.py`
- `punjab_estamp/migrations/0001_configuration_models.py`
- `punjab_estamp/migrations/0002_lease_estamp_workflow.py`
- `punjab_estamp/tests/__init__.py`
- `punjab_estamp/tests/test_workflow_constraints.py`
- `properties/migrations/0035_property_punjab_zila_tehsil_staging.py`

### Backups

Timestamped copies of every pre-existing file changed in this phase are stored under:

- `backups/punjab_estamp_phase1_20260926_045948/`

### Items awaiting review

- Snapshot payloads are split into applicant, property, and portal JSON fields so portal values remain frozen without duplicating a large number of schema columns. Exact snapshot construction and immutability begin in Phase 5.
- `default_continuation_sheets` is stored on Purpose, avoiding a separate settings model for one value.
- `last_synced_at` is stored on District because the future Tehsil sync is initiated per District.
- The old Property `zila` field remains intact; no mapping or destructive field swap has occurred.

Stop here. Phase 2 seed migration must not start until the user approves this Phase 1 checkpoint.

## 2026-09-26 — Phase 2: Verified Punjab Configuration Seed

### Work completed

Created and locally applied the reproducible verified configuration seed migration:

- `punjab_estamp/migrations/0003_seed_verified_configuration.py`

The migration uses the historical Django models and `get_or_create()` keyed by stable portal values. It creates missing verified records but does not overwrite an existing administrator-edited record during a later deployment. The reverse operation is intentionally non-destructive so reversing this data migration alone cannot delete configuration that may already be referenced or customized.

Only verified Rawalpindi Tehsils were seeded. No Tehsil portal value was guessed or fabricated for another District.

### Verified data applied to the local MySQL database

Districts — 41:

- Attock=`19`
- Bahawalnagar=`16`
- Bahawalpur=`15`
- Bhakkar=`32`
- Chakwal=`21`
- Chiniot=`14`
- Dera Ghazi Khan=`26`
- Faisalabad=`11`
- Gujranwala=`1`
- Gujrat=`2`
- Hafizabad=`5`
- Jhelum=`20`
- Jhang=`13`
- Kasur=`8`
- Khanewal=`25`
- Khushab=`33`
- Kot Addu=`38`
- Lahore=`7`
- Layyah=`27`
- Lodhran=`24`
- Mandi Bahauddin=`6`
- Mianwali=`31`
- Multan=`22`
- Murree=`40`
- Muzaffargarh=`29`
- Nankana Sahib=`9`
- Narowal=`4`
- Okara=`35`
- Pakpattan=`36`
- Rahim Yar Khan=`17`
- Rajanpur=`28`
- Rawalpindi=`18`
- Sahiwal=`34`
- Sargodha=`30`
- Sheikhupura=`10`
- Sialkot=`3`
- Talagang=`41`
- Taunsa=`42`
- Toba Tek Singh=`12`
- Vehari=`23`
- Wazirabad=`39`

Rawalpindi Tehsils — 7:

- Gujar Khan=`80`
- Kahuta=`82`
- Kallar Syedan=`84`
- Rawalpindi=`72`
- Rawalpindi Cantt=`176`
- Rawalpindi Saddar=`177`
- Taxila=`83`

Relations — 9:

- F/O=`12`
- M/O=`20`
- S/O=`33`
- D/O=`34`
- H/O=`35`
- W/O=`36`
- Widow of=`37`
- Guardian=`38`
- Representative From=`39`

Default lease Purpose — 1:

- Name: `AGREEMENT OR MEMORANDUM OF AN AGREEMENT - 5(ccc)`
- Portal value: `208`
- Denomination: `100`
- Default continuation sheets: `1`
- `is_default_for_lease=True`

Direct database counts after migration:

- Districts: 41
- Rawalpindi Tehsils: 7
- Relations: 9
- Purposes: 1
- Default lease Purposes: 1

### Migrations applied locally

The migration plan was inspected before execution and contained only these expected new operations:

- `punjab_estamp.0001_configuration_models` — applied successfully.
- `properties.0035_property_punjab_zila_tehsil_staging` — applied successfully.
- `punjab_estamp.0002_lease_estamp_workflow` — applied successfully.
- `punjab_estamp.0003_seed_verified_configuration` — applied successfully.

No unrelated pending migration was applied. The migration command displayed the repository's pre-existing MySQL warning for the conditional constraint on `smart_meter.EnergySystemMeterAssignment`; this warning is unrelated to Punjab e-Stamp and was not changed.

### Checks

- Seed constant counts before migration: 41 Districts, 7 Tehsils, 9 Relations, and 1 Purpose.
- Direct post-migration database dump: all names and portal values matched the verified input above.
- `python manage.py check` — passed with no issues.
- `python manage.py makemigrations --check --dry-run` — passed; no model/migration drift detected.
- `git diff --check` — passed.

### Files changed

- Created `punjab_estamp/migrations/0003_seed_verified_configuration.py`.
- Updated `PUNJAB_ESTAMP_PROGRESS.md`.

No model, form, template, view, `.env`, secret, deployment, nginx, or systemd file was changed in Phase 2.

### Backups

The existing progress log was backed up before modification under:

- `backups/punjab_estamp_phase2_20260926_050510/`

### Next checkpoint

Stop here. Phase 3 Property Zila/Tehsil data migration and UI work must not begin until the user approves this Phase 2 checkpoint.

## 2026-09-26 — Phase 3: Property Zila / Tehsil Migration and UI

### Work completed

Completed the safe staged conversion of Property Zila from free text to the Punjab District configuration model:

- `Property.zila` is now a nullable FK to `PunjabEStampDistrict`.
- `Property.tehsil` is a nullable FK to `PunjabEStampTehsil`.
- No separate `Property.district` field was created.
- The original textual Zila value is retained in non-editable `Property.zila_legacy` for migration audit and fallback investigation. It is not shown as a competing editable location field.
- `Property.clean()` rejects a Tehsil whose District does not match `Property.zila`.
- Existing agreement/police-verification displays continue to render the District name because the configuration model has a stable string representation.

### Data migration and local result

Created and applied:

- `properties/0036_map_property_zila_tehsil.py`
- `properties/0037_finalize_property_zila_fk.py`

Migration `0036`:

1. Trims and case-folds the old textual Zila.
2. Matches only a complete normalized District name.
3. Does not use fuzzy or partial District matching.
4. Leaves unknown values unmapped.
5. Assigns Rawalpindi Tehsil only when the mapped District is Rawalpindi and existing Zila/city/address fields contain Rawalpindi evidence.
6. Does not overwrite a District or Tehsil already configured during the staging period.

The migration was applied separately from the final rename and inspected at the raw database-column level before `0037` was allowed to run.

All six local Property records mapped successfully:

- F56: legacy Rawalpindi -> District Rawalpindi (`18`) -> Tehsil Rawalpindi (`72`)
- F54: legacy Rawalpindi -> District Rawalpindi (`18`) -> Tehsil Rawalpindi (`72`)
- F35: legacy Rawalpindi -> District Rawalpindi (`18`) -> Tehsil Rawalpindi (`72`)
- F56 Basement: legacy Rawalpindi -> District Rawalpindi (`18`) -> Tehsil Rawalpindi (`72`)
- Motor (Meter): legacy Rawalpindi -> District Rawalpindi (`18`) -> Tehsil Rawalpindi (`72`)
- H9: legacy Rawalpindi -> District Rawalpindi (`18`) -> Tehsil Rawalpindi (`72`)

Unmapped local records: 0.

Migration `0037` then renamed the old text field to `zila_legacy` and the staged FK from `zila_fk` to the final `zila` name. Both migrations are applied in local MySQL.

### Property form behavior

Updated the active `properties/property_form.html` page without changing its existing compact Bootstrap layout:

- Replaced editable free-text Zila with the `Zila / District` model dropdown.
- Added the Tehsil model dropdown beside it.
- Only active Districts are offered, while an existing inactive saved District remains visible during edit.
- The Tehsil queryset is restricted server-side to the selected District.
- Changing District calls the authenticated `properties:property_tehsils` endpoint and refreshes Tehsil options without Kendo controls.
- The endpoint returns only active Tehsils belonging to the requested active District.
- New Property forms default to District Rawalpindi (`18`) and Tehsil Rawalpindi (`72`).
- An explicit caller-provided initial District takes precedence over that default.
- Both location choices are required by the Property form.
- Bound forms and edit forms preserve the correct District-specific choice set.
- Cross-District Tehsil submissions are rejected by the form and model validation.
- The Property detail page now displays `Zila / District` and `Tehsil`.

### Files changed

Existing files modified:

- `properties/models.py`
- `properties/forms.py`
- `properties/views.py`
- `properties/urls.py`
- `properties/templates/properties/property_form.html`
- `properties/templates/properties/property_detail.html`
- `PUNJAB_ESTAMP_PROGRESS.md`

New files:

- `properties/migrations/0036_map_property_zila_tehsil.py`
- `properties/migrations/0037_finalize_property_zila_fk.py`
- `properties/test_punjab_location.py`

The unused duplicate `property_form_responsive.html` template was not modified; both active create/update views explicitly use `property_form.html`.

### Tests and checks

- `properties.test_punjab_location` — 8 tests passed.
  - Rawalpindi defaults.
  - Explicit initial District precedence.
  - District-filtered bound Tehsil queryset.
  - Cross-District Tehsil rejection.
  - Valid matching District/Tehsil.
  - Authenticated API filtering and exclusion of inactive Tehsils.
  - Authentication requirement.
  - Active Property create page rendering and linked-control hooks.
- `punjab_estamp.tests.test_workflow_constraints` — 3 tests passed together with the focused location suite.
- Existing `properties.tests` regression suite — 29 tests passed.
- `python manage.py check` — passed with no issues.
- `python manage.py makemigrations --check --dry-run` — passed; no drift detected.
- `git diff --check` — passed.

### Backups

Timestamped backups of every pre-existing file changed in this phase are under:

- `backups/punjab_estamp_phase3_20260926_051634/`

### Decisions and notes

- Retaining `zila_legacy` is a deliberate safety improvement over dropping the text immediately: production records with an unknown legacy value will not lose their original data, while the only editable/canonical field is the District FK named `zila`.
- Database fields remain nullable for backward-compatible migration safety; the normal Property form requires both values. Phase 5 will independently block Punjab workflow launch if either is missing.
- The MySQL migration command again displayed the unrelated pre-existing conditional-constraint warning for `smart_meter.EnergySystemMeterAssignment`. No smart-meter code was changed.

Stop here. Phase 4 Tenant relation conversion and Property owner/caretaker Tenant linkage must not begin until the user approves this Phase 3 checkpoint.

## 2026-09-26 - Phase 4: Tenant Relations and Property Identity Links

### Work completed

Converted the agreement identity relation on `Tenant` to the verified Punjab e-Stamp list and linked Property owners/caretakers to existing Tenant/person records:

- `Tenant.relation` is now a nullable `PROTECT` FK to `PunjabEStampRelation`.
- Added non-editable `Tenant.relation_legacy` so every original text value remains available for audit and fallback rendering.
- `Tenant.get_full_name_agreement()` uses the Punjab relation name first and safely falls back to `relation_legacy`.
- The normal Tenant form and public registration form now load active Punjab relation choices from the database.
- New public registration JSON stores the relation PK. The approval service remains compatible with historical submissions containing values such as `S/O.` and `D/O.`.
- Reviewer inline editing now presents the verified Punjab relation list instead of accepting arbitrary text.
- The agreement-party quick-create endpoint resolves its existing dotted relation values through the Punjab relation list and rejects unknown values.
- Added nullable `Property.owner_tenant` and `Property.caretaker_tenant` FKs to `Tenant`, both using `SET_NULL` so deleting a linked person does not delete the Property.
- Existing Property owner/caretaker text, CNIC, phone, address, relation, and photo fields were retained. No legacy identity field was deleted.

### Migrations and local data result

Created and applied:

- `tenants/0031_tenant_punjab_relation_staging.py`
- `tenants/0032_map_tenant_punjab_relation.py`
- `properties/0038_property_tenant_links.py`
- `properties/0039_backfill_property_tenant_links.py`

Tenant relation mapping is normalization-only: surrounding whitespace and periods are ignored, but no semantic guesses are made.

Final local Tenant relation counts:

- `S/O`: 135
- `D/O`: 4
- `W/O`: 8
- Unmapped/null FK: 3

The three unrecognized original values are preserved exactly in `relation_legacy` and require manual correction if used for Punjab e-Stamp:

- `B/O`: 1
- `Mr.`: 1
- `On Behalf`: 1

Property identity backfill uses only a unique, exact digits-only CNIC match. It does not use name matching:

- Owner links: 6 of 6 local Properties.
- Caretaker links: 4 of 4 Properties with a populated caretaker CNIC.
- Property 7 retains legacy owner name `Aneela Ali`, but its owner CNIC exactly matches Tenant 99 (`Sohail Issa`), so the authoritative link is Tenant 99. The discrepancy remains visible in the preserved legacy field.
- Properties 1, 6, and 8 link caretaker CNIC `1520232529835` to Tenant 174.
- Property 7 links its caretaker CNIC `4210120080103` to Tenant 99.
- Properties 9 and 10 have no caretaker CNIC and remain without a caretaker link.

### Property UI behavior

The existing compact Property form now includes searchable Select2 controls for Owner Tenant and optional Caretaker Tenant:

- Active Tenants are offered; an already selected inactive Tenant remains available while editing.
- Selecting a Tenant calls an authenticated identity endpoint and immediately displays name, Punjab relation, CNIC, phone, email, and address.
- On save, the linked Tenant supplies the legacy compatibility fields, so a user does not have to retype the identity.
- A Property still supports the old owner fields when no Tenant link is selected, preserving backward compatibility.
- The Property detail page identifies linked owner/caretaker Tenant records and continues to show the legacy snapshot fields.

### Files changed in Phase 4

Existing files modified:

- `tenants/models.py`
- `tenants/forms.py`
- `tenants/views.py`
- `tenants/services/registration_workflow.py`
- `tenants/templates/tenants/registration_submission_detail.html`
- `tenants/tests.py`
- `properties/models.py`
- `properties/forms.py`
- `properties/views.py`
- `properties/urls.py`
- `properties/templates/properties/property_form.html`
- `properties/templates/properties/property_detail.html`
- `leases/views.py`
- `leases/tests.py`
- `views.py` (the repository's parallel legacy agreement-party endpoint)
- `PUNJAB_ESTAMP_PROGRESS.md`

New files:

- `tenants/migrations/0031_tenant_punjab_relation_staging.py`
- `tenants/migrations/0032_map_tenant_punjab_relation.py`
- `properties/migrations/0038_property_tenant_links.py`
- `properties/migrations/0039_backfill_property_tenant_links.py`
- `properties/test_punjab_tenant_links.py`

### Tests and checks

- `python manage.py check` - passed with no issues.
- `python manage.py makemigrations --check --dry-run` - passed; no model/migration drift.
- Python compilation for the touched Python modules - passed.
- `properties.test_punjab_tenant_links` - 4 tests passed.
- `properties.test_punjab_location` plus the Phase 4 link suite - 12 tests passed.
- Existing `properties.tests` - 29 tests passed.
- Existing `tenants.tests` - 73 tests passed.
- `leases.tests.AgreementPartyAjaxTests` - 4 tests passed.
- The complete `leases.tests` run passed 40 of 41 tests. Its only failure is the unrelated move-in billing proration expectation (`5940.00` produced versus `5922.58` expected); Phase 4 does not modify that billing path.
- `git diff --check` - passed. Only line-ending notices were emitted on Windows.

### Backups

Timestamped backups of every pre-existing file changed in Phase 4 are under:

- `backups/punjab_estamp_phase4_20260926_132754/`

No `.env`, secret, Django settings, deployment, nginx, systemd, permission, or production-server file was changed in Phase 4.

Stop here. Phase 5 workflow/service layer and Agreement UI state work must not begin until the user approves this Phase 4 checkpoint.

## 2026-09-26 - Phase 5: Workflow Service and Agreement UI States

### Work completed

Implemented the Punjab e-Stamp workflow/service layer and its compact Agreement-page status card without adding any Punjab portal automation or a second PDF processor.

- Added an atomic workflow service around the existing `LeaseEStampWorkflow` model.
- A workflow is prepared per lease renewal/history record, preserving the Phase 1 uniqueness constraint.
- Applicant selection follows the agreed priority: linked caretaker Tenant, legacy caretaker identity, linked owner Tenant, then legacy owner identity.
- The generated reason/comment is exactly `<UNIT NAME> - <TENANT FULL NAME>`.
- The first successful challan capture freezes the application snapshot. Later edits to Property, Tenant, Punjab mappings, or purpose configuration cannot alter or block that historical workflow.
- A new lease renewal/history record creates a separate workflow and uses the then-current identity and configuration.
- Repeating the same challan/PSID capture is idempotent. Attempting to overwrite an active workflow with different identifiers is rejected.
- Marking a challan invalid and creating a replacement archives its identifiers, stamp details, error, and timestamps in `challan_history`, increments the replacement count, clears the active transaction identifiers, and preserves the original applicant/configuration snapshot.
- Added controlled transitions for prepared, challan generated, paid, stamp issued, uploaded, invalid, and replacement-ready states.
- The WhatsApp payment message uses the immutable applicant phone snapshot and places the PSID alone on a copy-friendly line.
- The existing agreement-party quick-add relation selector now loads active Punjab relations from the database; its existing server-side compatibility with historical dotted relation values is retained.

### Immutable snapshot captured at first challan

The workflow freezes all submission-critical values:

- applicant source and linked Tenant ID
- applicant name, CNIC, phone, email, and address
- Punjab relation name and portal value
- relation person's/father's name
- District name and portal value
- Tehsil name and portal value
- Purpose name and portal value
- denomination
- continuation text
- generated reason/comment

Before first challan capture, preparation validates the District/Tehsil pairing, active portal mappings, purpose, applicant name, 13-digit CNIC, phone, mapped relation, relation person/father name, and address. Exact missing items are returned to the Agreement UI.

### Agreement UI states

Added a compact responsive Punjab e-Stamp card to the active `edit_clause.html` Agreement page with six user-facing states:

1. Ready to Create - shows `Create Stamp Paper`, or a modal listing exact missing configuration with a Property edit link.
2. Challan Generated / Not Paid - shows challan and PSID plus an active WhatsApp payment action.
3. Paid - shows the paid state and the next portal action.
4. Stamp Issued - shows the stamp number and download/continue action.
5. Uploaded - links to the resulting e-Stamp document when present.
6. Invalid - provides an idempotent `Create Replacement Challan` action.

Portal-dependent controls are deliberately disabled and labelled for Phase 6. The Phase 5 UI does not request or expose an OTP or PDF PIN.

### Workflow endpoints

Added authenticated, lease-permission-checked endpoints under `punjab-estamp/` in both canonical and existing `/tms/` URL layouts:

- prepare workflow (`POST`)
- prepare replacement (`POST`)
- retrieve workflow state (`GET`)

The state response omits OTP and PIN data. It supplies the immutable captured application state needed by the future Phase 6 browser helper.

### Files changed in Phase 5

Existing files modified:

- `leases/views.py`
- `leases/templates/leases/edit_clause.html`
- `tms/urls.py`
- `PUNJAB_ESTAMP_PROGRESS.md`

New files:

- `punjab_estamp/services/__init__.py`
- `punjab_estamp/services/workflow.py`
- `punjab_estamp/views.py`
- `punjab_estamp/urls.py`
- `punjab_estamp/tests/test_workflow_service.py`

No model or migration was added or changed in Phase 5.

### Tests and checks

- Phase 5 workflow service plus workflow constraint tests - 11 tests passed.
- Existing manual e-Stamp PDF/PIN regression suite together with Tenant/Property link regressions - 104 tests passed.
- Focused Phase 5, Agreement AJAX, and existing manual e-Stamp regression run - 39 tests passed.
- Tests cover all applicant priority/fallback paths, missing-relation blocking, exact comment generation, prepare/capture idempotency, immutable first-challan snapshots, new-renewal freshness, payment/stamp/upload transitions, invalid replacement history, WhatsApp recipient/message formatting, state API secret exclusion, all six UI states, and the database-backed quick-add relation selector.
- `python manage.py check` - passed with no issues.
- `python manage.py makemigrations --check --dry-run` - passed; no model/migration drift.
- `git diff --check` - passed. Only Windows line-ending notices were emitted.
- A read-only audit of the first 20 local lease records resolved linked caretaker applicants and reported no missing Punjab configuration for those records. It did not create any real workflow or challan.

### Backups and scope boundary

Timestamped backups of every pre-existing file changed in Phase 5 are under:

- `backups/punjab_estamp_phase5_20260926_140946/`

No `.env`, secret, deployment, nginx, systemd, permission, or production-server file was changed in Phase 5. No live Punjab government traffic, browser automation, OTP handling, PDF capture, or PDF-processing pipeline was implemented. The existing generic manual `estamp_paper` PDF + PIN path remains the sole processing path and its regression tests pass.

Stop here. Phase 6 browser automation and Punjab portal integration must not begin until the user approves this Phase 5 checkpoint.

## 2026-09-26 - Phase 6: Punjab Portal Browser Helper

### Work completed

Implemented the Punjab portal integration as an installable userscript plus a same-origin TMS bridge. No live Challan or Stamp was created during development or testing.

- Added a permission-checked launch endpoint that prepares/reuses the existing workflow and returns a browser-helper payload.
- Launch payloads are always marked `dryRun: true`. The helper refuses to fill a Challan if that guard is absent or false.
- New-Challan launches use a signed, four-hour server token containing the exact applicant/property/portal values sent to Punjab. When the resulting Challan is captured, that signed payload becomes the immutable first-Challan snapshot. This prevents a concurrent Property/Tenant edit from making the TMS snapshot differ from the values actually populated in Punjab.
- The Agreement page opens Punjab in a separate tab and exchanges configuration/events with the helper through a channel-scoped `postMessage` handshake.
- The TMS page accepts helper messages only from the exact Punjab HTTPS origin and the exact popup window it opened. TMS API and upload calls remain same-origin and retain normal authentication, permission, and CSRF checks; no CORS reachability assumption is used.
- The helper persists active flow state only in Punjab-tab `sessionStorage`, never `localStorage`. Applicant values are not placed in a URL or query string.

### Dry-run and portal form behavior

The Challan helper:

- Sets Applied Through to Self.
- Populates District, Tehsil, Applicant Name, CNIC, Relation, Relation Name, Phone, Email, Address, Purpose, Denomination, and Reason from the workflow snapshot.
- Uses the stored portal values for Kendo controls.
- Triggers Kendo and native input/keyup/change/blur events to synchronize Punjab validation.
- Waits for the expected Tehsil value and label after District changes before selecting it.
- Stops after filling the form and displays: `TMS dry run complete. Review every field, solve CAPTCHA, then click Punjab NEXT yourself. TMS has not submitted the form.`
- Contains no code that locates or clicks Punjab's final NEXT/submit control. CAPTCHA always remains manual.

Dry-run confirmation performed locally:

- The launch API test confirms `dryRun` is always true.
- The browser-helper safety test confirms the helper rejects a missing/false dry-run flag.
- The safety test confirms there is no `btnNext` lookup/click in the production userscript.
- No live portal submission was attempted.

### Challan, payment, retrieval, and exact matching

- Parses only the documented `Your Challan Number is ... And Your PSID is ...` result and posts it to the existing workflow.
- Repeated capture of the same Challan/PSID remains idempotent through the Phase 5 service; different identifiers remain rejected.
- Updated the WhatsApp message to the requested shape, including Property, Unit, Tenant, Challan, amount, and the PSID in inline backticks for easy copying. It still uses the immutable applicant phone snapshot.
- `Check / Continue` reopens the existing Challan path; it never creates a replacement or new Challan.
- Retrieval fills CNIC and mobile, leaves email unchecked, and requests OTP without bypassing Punjab verification.
- Route matching is ordered from most specific to least specific: `StampRetrievalbyCNIC`, `SearchChallan`, then `StampRetrieval`.
- Result-table parsing requires a normalized exact Challan-number match. It never selects the first/newest row and records a portal error if no exact row exists.
- After an exact match, TMS marks the existing Challan paid and records the exact Stamp number idempotently.
- The helper contains no Generate/Regenerate/Cancel-and-Regenerate action. If Punjab reports an existing issued/generated Stamp, it only continues the download path.

### OTP/PIN handling

- Manual six-box and combined-field OTP entry is detected without changing Punjab's verification behavior.
- Browser/native WebOTP is attempted only when supported, is wrapped in failure handling, and manual entry always remains available.
- The six-digit value is stored only under a workflow-scoped Punjab-tab `sessionStorage` key.
- OTP/PIN is never included in launch/state APIs, URLs, database fields, application logs, or browser console output.
- It is transferred in memory to the still-open TMS page only when a PDF is captured, used once as the existing upload endpoint's `estamp_password`, removed from multipart state after the request, and cleared from Punjab `sessionStorage` after successful upload.

### PDF capture and existing processing handoff

- Added `installPdfCapture()` to observe same-origin Punjab `fetch` PDF responses, XHR PDF/blob/ArrayBuffer responses, blob links, and direct PDF links.
- A captured PDF is transferred in memory to the authenticated TMS opener and posted as multipart data to the existing `leases:lease_file_upload` endpoint.
- The existing `normalize_estamp_pdf(upload, estamp_password)` function remains the only PDF/PIN processor. No second processor was created.
- The upload endpoint now recognizes a valid Punjab workflow ID, locks that workflow transactionally, requires `stamp_issued`, limits the automated handoff to one file, associates the existing `LeaseDocument` with the exact `LeaseRenewal`, then calls the Phase 5 `mark_uploaded()` service.
- If the workflow already has its uploaded document, a repeated automatic upload returns that document idempotently and creates no duplicate `estamp_paper` record.
- An encrypted-PDF integration test confirms that the captured OTP/PIN unlocks the PDF through the existing processor, all output is stored unencrypted, the workflow becomes uploaded, and a second handoff leaves exactly one document.

### Agreement UI and connectivity behavior

- Enabled Create Stamp Paper, Check / Continue, Continue Stamp Process, and Download / Continue buttons according to the existing six-state card.
- Added an Install/update browser helper link to the card.
- Added the requested timeout dialog with Try Again, Open Punjab e-Stamp, and Cancel actions.
- The dialog says the portal could not be reached or did not respond and suggests checking VPN/proxy and internet connectivity; it does not claim that a VPN was detected.

### Files changed in Phase 6

Existing files modified:

- `punjab_estamp/services/workflow.py`
- `punjab_estamp/views.py`
- `punjab_estamp/urls.py`
- `punjab_estamp/tests/test_workflow_service.py`
- `leases/views_lease_files.py`
- `leases/templates/leases/edit_clause.html`
- `PUNJAB_ESTAMP_PROGRESS.md`

New file:

- `punjab_estamp/static/punjab_estamp/punjab_portal_helper.user.js`

No model or migration was added or changed in Phase 6.

### Tests and checks

- Expanded Punjab workflow service/browser integration suite - 12 tests passed.
- Broad related run covering all Punjab tests, manual e-Stamp tests, Agreement AJAX, Property/Tenant links, and Tenant tests - 123 tests passed.
- Final focused Punjab plus existing manual e-Stamp run - 39 tests passed.
- The automatic encrypted-PDF handoff and repeat-upload idempotency test passed.
- Signed launch snapshot and exact-Challan server rejection/acceptance tests passed.
- Dry-run, session-only OTP, route-order, PDF-capture installation, and no-final-NEXT-control tests passed.
- Python compilation passed.
- `python manage.py check` - passed with no issues.
- `python manage.py makemigrations --check --dry-run` - passed; no model/migration drift.
- `git diff --check` - passed. Only Windows line-ending notices were emitted.
- Node.js is not installed in this environment, so `node --check` could not be run; the helper was instead covered by Django static-content safety tests. Live DOM behavior still requires the required human dry-run against the current Punjab page before any real submission.

### Backups, safety, and remaining live limitation

Timestamped backups of every pre-existing file changed in Phase 6 are under:

- `backups/punjab_estamp_phase6_20260926_203043/`

No `.env`, secret, model, migration, deployment, nginx, systemd, permission, or production-server configuration was changed. CAPTCHA and OTP were not bypassed. No live submission was attempted.

The helper covers the requested fetch/XHR/blob/PDF-link capture paths. A browser-native file download produced entirely outside those page-visible paths cannot be proven interceptable without a controlled live dry-run on the current Punjab portal. The user must install/enable the userscript and keep the originating TMS Agreement tab open. The first real portal validation must remain dry-run: verify every populated field, confirm the helper stops before NEXT, and only then decide whether to click Punjab's action manually.

Stop here. Phase 7 all-Punjab Tehsil synchronization must not begin until the user approves this Phase 6 checkpoint.
