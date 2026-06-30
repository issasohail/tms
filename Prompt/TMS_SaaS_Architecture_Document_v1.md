# TMS SaaS Architecture Document

**Project:** Tenant Management System (TMS)  
**Document Type:** SaaS Architecture & Implementation Blueprint  
**Version:** 1.0  
**Prepared For:** TMS Development / Codex Implementation  
**Status:** Draft for implementation planning  

---

# 1. Executive Summary

TMS is evolving from a single-operator tenant management system into a SaaS-style, multi-tenant platform. The current system uses Django authentication and permissions, but users with the correct permissions can access system-wide Properties, Units, Leases, Tenants, Invoices, Payments, and related records.

This document defines the architecture required to support:

- Platform users who operate TMS.
- Master accounts who are paying landlords or property managers.
- Sub-users created by master accounts.
- Per-unit quota and licensing.
- Role-based permissions.
- Centralized queryset scoping.
- Account status control.
- Subscription placeholder for future billing.
- Master self-service portal.
- In-app support messaging.
- WhatsApp-based sub-user password reset.
- Audit logging for sensitive actions.

The goal is to add SaaS-grade tenant isolation and account hierarchy without redesigning the whole project and without breaking existing `user.has_perm()` checks.

---

# 2. Product Vision

TMS should become a commercial SaaS platform where multiple landlords or property managers can use the same application while their data remains isolated.

A master account should be able to manage only its own portfolio. Sub-users should be limited to the master account that created them. Platform staff should retain full operational control.

The system should support future commercial growth, including subscription plans, account suspension, per-unit billing, customer-specific WhatsApp/email settings, and self-service account management.

---

# 3. Core Design Principles

## 3.1 Tenant Isolation First

Every business record must be scoped before it is shown, edited, exported, or acted upon.

Template hiding is not security. Querysets must be filtered server-side.

## 3.2 Minimal Data Model Surface

Do not add `master_account` or tenant ownership fields to every model.

Scoping should flow through:

```text
Master Account
    ↓
Property
    ↓
Unit
    ↓
Lease / Tenant / Invoice / Payment / Maintenance / Expense
```

This keeps the design clean and avoids unnecessary schema changes.

## 3.3 Preserve Existing Permission Checks

The current codebase uses Django permission checks through `user.has_perm()`.

The new Role system must not replace Django permissions. It should only provide reusable named permission bundles that sync into `user_permissions`.

## 3.4 Platform Users Are Not Customers

Staff and superusers are platform users. They must never be treated as master accounts.

This is critical because both platform users and master accounts may have `master_account=None`, but they are not the same kind of account.

## 3.5 Safe Production Migration

Existing production data must continue working after deployment.

Existing non-staff users should become master accounts by logic. Existing staff and superusers should remain platform users.

## 3.6 Phased Implementation

The feature should be implemented in phases to reduce risk:

- Phase 1: Core account hierarchy, roles, licensing, scoping, status, subscription placeholder.
- Phase 2: Master portal, support messaging, WhatsApp reset.
- Future: Billing automation, payment gateway, subscription enforcement, feature plans.

---

# 4. Account Tiers

TMS will support three account tiers.

## 4.1 Platform Users

Platform users are internal TMS operator accounts.

They are identified by:

```python
user.is_staff or user.is_superuser
```

Platform users:

- are not customers
- are not master accounts
- can manage all data if permissions allow
- can create master accounts
- can assign licenses and quotas
- can transfer properties
- can review audit logs
- can respond to support messages

## 4.2 Master Accounts

Master accounts are paying landlords or property managers.

They:

- own/manage one or more Properties in the system
- have a unit quota
- may have explicitly licensed units if over quota
- may create sub-users
- may have their own WhatsApp/email config
- may access a master portal
- may be suspended, expired, pending, cancelled, or active

## 4.3 Sub-users

Sub-users are created under master accounts.

They:

- belong to exactly one master account
- inherit access limits from the master
- can optionally be scoped to selected managed properties
- do not manage other users
- are blocked when their master account is suspended or inactive

---

# 5. Account Model Design

## 5.1 New Account Fields

Add the following fields to `accounts.Account`.

```python
master_account = models.ForeignKey(
    "self",
    null=True,
    blank=True,
    on_delete=models.PROTECT,
    related_name="sub_users",
    db_index=True,
)

unit_quota = models.PositiveIntegerField(default=0)

licensed_units = models.ManyToManyField(
    "properties.Unit",
    blank=True,
    related_name="licensed_to_accounts",
)

managed_properties = models.ManyToManyField(
    "properties.Property",
    blank=True,
    related_name="managed_by_subusers",
)

role = models.ForeignKey(
    "accounts.Role",
    null=True,
    blank=True,
    on_delete=models.SET_NULL,
)

status = models.CharField(
    max_length=20,
    choices=Status.choices,
    default=Status.ACTIVE,
)

whatsapp_api_key = models.CharField(max_length=255, blank=True, null=True)
whatsapp_sender_number = models.CharField(max_length=50, blank=True, null=True)
email_host_user = models.CharField(max_length=255, blank=True, null=True)
email_host_password = models.CharField(max_length=255, blank=True, null=True)
```

## 5.2 Account Status Choices

```python
class Status(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    SUSPENDED = "SUSPENDED", "Suspended"
    EXPIRED = "EXPIRED", "Expired"
    PENDING = "PENDING", "Pending"
    CANCELLED = "CANCELLED", "Cancelled"
```

Only master accounts use this field directly. Sub-users inherit login blocking from their master.

## 5.3 Tier Helper Properties

Never rely only on `master_account is None`.

Add explicit helpers:

```python
@property
def is_platform_user(self):
    return self.is_superuser or self.is_staff

@property
def is_master_account(self):
    return self.master_account_id is None and not self.is_platform_user

@property
def is_sub_user(self):
    return self.master_account_id is not None

def effective_master(self):
    if self.is_master_account:
        return self
    if self.is_sub_user:
        return self.master_account
    return None
```

Important:

- Platform users return `None` from `effective_master()`.
- Code using `effective_master()` must handle `None` as platform/default behavior.

---

# 6. Property Ownership

## 6.1 Property Model Field

Add this field to `properties.Property`.

```python
master_account = models.ForeignKey(
    "accounts.Account",
    null=True,
    blank=True,
    on_delete=models.PROTECT,
    related_name="owned_properties",
    db_index=True,
    limit_choices_to={"master_account__isnull": True},
)
```

This field represents system-level management ownership.

It must stay separate from legal landlord fields such as:

- `owner_name`
- `owner_cnic`
- `owner_phone`
- existing property owner descriptive fields

Do not rename or change those fields.

## 6.2 Validation Rule

Whenever `Property.master_account` is set, validate that the selected account is a true master account:

```python
target.is_master_account is True
```

Do not rely only on `limit_choices_to`.

---

# 7. Master Account Query Helper

Create reusable helper in `accounts/scoping.py`.

```python
def master_account_queryset():
    from accounts.models import Account

    return Account.objects.filter(
        master_account__isnull=True,
        is_staff=False,
        is_superuser=False,
    )
```

Use this everywhere a master account is selected:

- property ownership forms
- licensing screens
- transfer screens
- master portal staff selector
- support inbox
- subscription admin views

---

# 8. Accessible Units and Properties

## 8.1 Master Account Licensing Rule

Licensing has two modes:

### Under quota

If the master account's total current Unit count across owned Properties is less than or equal to `unit_quota`, the master gets automatic access to all units under owned properties.

No manual licensing is required.

### Over quota

If total Unit count exceeds `unit_quota`, the system uses `licensed_units` as the explicit allowed access list.

This allows small customers to avoid manual setup, while large customers can be controlled by explicit unit selection.

## 8.2 Account.accessible_units()

```python
def accessible_units(self):
    from properties.models import Unit

    if self.is_platform_user:
        return Unit.objects.all()

    if self.is_master_account:
        owned_units = Unit.objects.filter(property__master_account=self)

        if owned_units.count() <= self.unit_quota:
            return owned_units

        return self.licensed_units.all()

    if self.is_sub_user:
        master_units = self.master_account.accessible_units()
        managed = self.managed_properties.all()

        if managed.exists():
            return master_units.filter(property__in=managed)

        return master_units

    return Unit.objects.none()
```

## 8.3 Account.accessible_properties()

```python
def accessible_properties(self):
    from properties.models import Property

    if self.is_platform_user:
        return Property.objects.all()

    return Property.objects.filter(
        units__in=self.accessible_units()
    ).distinct()
```

---

# 9. Queryset Scoping Layer

Create centralized scoping helpers.

## 9.1 scoped_by_unit

```python
def scoped_by_unit(qs, user, unit_field="unit"):
    if user.is_platform_user:
        return qs

    lookup = f"{unit_field}__in"
    return qs.filter(**{lookup: user.accessible_units()})
```

## 9.2 scoped_by_property

```python
def scoped_by_property(qs, user, property_field="property"):
    if user.is_platform_user:
        return qs

    lookup = f"{property_field}__in"
    return qs.filter(**{lookup: user.accessible_properties()})
```

## 9.3 Application

Apply these helpers to list, detail, edit, export, and action views in:

- properties
- units
- tenants
- leases
- invoices
- payments
- expenses
- maintenance
- media/export flows where business records are loaded

Do not depend on template hiding.

---

# 10. Role Model

## 10.1 Purpose

The Role model provides named permission bundles.

Roles do not replace Django permissions. They sync into `user_permissions` so existing permission checks continue to work.

## 10.2 Model

```python
class Role(models.Model):
    name = models.CharField(max_length=100)

    permissions = models.ManyToManyField(
        Permission,
        blank=True,
    )

    is_system_role = models.BooleanField(default=False)

    created_by_master = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="created_roles",
    )
```

## 10.3 System Roles

Create data migration for:

- Admin Full
- Manager
- Data Entry
- Viewer

The permission sets must be extracted from the existing JS preset buttons in:

- `user_access_form.html`
- `group_access_form.html`

Do not guess permission mappings.

## 10.4 Permission Sync

When `Account.role` is set or changed:

- sync `user_permissions` to match `role.permissions`
- keep `has_perm()` behavior unchanged

## 10.5 Permission Divergence

If a user’s permissions are manually changed after a role is assigned:

- keep the role FK
- do not auto-clear role
- show warning:

```text
Custom permissions differ from the assigned role.
```

Audit divergence events if practical.

---

# 11. Account Management

## 11.1 user_access_list

Access rules:

- platform users see all users
- master accounts see only their sub-users
- sub-users are denied entirely

For master account:

```python
Account.objects.filter(master_account=request.user)
```

## 11.2 user_access_create and user_access_update

If creator is master account:

- force `master_account=request.user`
- never trust POST value
- restrict property/unit checkboxes to creator’s accessible scope

If creator is platform user:

- allow selecting an existing master account
- allow creating a new master account
- use `master_account_queryset()` for dropdowns

## 11.3 Sub-user Managed Properties

For `managed_properties`:

- restrict queryset to master’s accessible properties
- validate server-side before save
- never allow a sub-user to be scoped to another master’s property

---

# 12. Licensing Screens

## 12.1 Permission

Add real permission:

```python
accounts.manage_licenses
```

Every licensing view must check:

```python
request.user.has_perm("accounts.manage_licenses")
```

and:

```python
if not request.user.is_platform_user:
    raise PermissionDenied
```

## 12.2 Master Account List

Create `master_account_list`.

Show all real master accounts using `master_account_queryset()`.

Display:

- account name
- property count
- sub-user count
- total owned unit count
- licensed unit count
- unit quota
- status
- subscription status if available

Flag:

```text
N over quota
```

when licensed count exceeds quota.

Do not auto-resolve over-quota states.

## 12.3 License Edit

Create `master_account_license_edit(pk)`.

Show:

- editable `unit_quota`
- properties owned by the master
- units under each property as checkboxes
- checked state from `licensed_units`

Server-side validation:

- every selected unit must belong to a property owned by that master
- selected count must be <= `unit_quota`
- invalid submissions must fail with no partial save

Client-side behavior:

- once checked count reaches quota, disable unchecked boxes
- checked boxes remain clickable so staff can swap units

Quota reduction:

- do not auto-revoke units
- show over-quota warning
- block further additions until corrected

---

# 13. Property Transfer

## 13.1 Permission

Add permission:

```python
properties.transfer_property
```

Require:

```python
request.user.is_platform_user
```

and permission check.

## 13.2 View

Create:

```python
property_transfer(request, pk)
```

Rules:

- platform users only
- POST only for final commit
- target master must come from `master_account_queryset()`

## 13.3 Transaction Behavior

Inside `transaction.atomic()`:

1. Save `property.master_account = new_master`.
2. Remove the property’s units from the old master’s `licensed_units`.
3. Remove the property from old master sub-users’ `managed_properties`.
4. Do not auto-add units to new master’s `licensed_units`.

Never modify:

- Unit
- Lease
- Tenant
- Invoice
- Payment
- Expense
- Maintenance
- media files

## 13.4 Confirmation

Before final commit, show:

- old master
- new master
- number of units
- number of active leases
- number of tenants
- explicit warning that no business data will be deleted or altered

## 13.5 Audit

Log:

- actor
- old master
- new master
- property id
- summary

---

# 14. Audit Log

## 14.1 Model

```python
class AuditLog(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )

    action = models.CharField(max_length=100)

    target_model = models.CharField(max_length=100)
    target_id = models.CharField(max_length=100)

    summary = models.TextField(blank=True)

    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
```

## 14.2 Minimum Audit Coverage

Audit:

- property transfers
- license changes
- quota changes
- master account creation
- sub-user creation
- role assignment changes
- permission divergence from role
- sub-user managed property changes
- password reset/change actions in Phase 2

## 14.3 Audit View

Create staff-only audit list.

Filters:

- actor
- action
- target model
- date range
- master account if relevant

---

# 15. WhatsApp and Email Configuration

## 15.1 Purpose

Master accounts may have their own WhatsApp and email credentials.

If blank, TMS should use platform defaults.

Do not duplicate fallback logic at every send site.

## 15.2 Helpers

Create helper functions.

```python
def get_whatsapp_config_for_user(user):
    master = user.effective_master() if user and not user.is_platform_user else None

    if master:
        return (
            master.whatsapp_api_key or settings.DEFAULT_WHATSAPP_API_KEY,
            master.whatsapp_sender_number or settings.DEFAULT_WHATSAPP_SENDER_NUMBER,
        )

    return (
        settings.DEFAULT_WHATSAPP_API_KEY,
        settings.DEFAULT_WHATSAPP_SENDER_NUMBER,
    )
```

```python
def get_email_config_for_user(user):
    master = user.effective_master() if user and not user.is_platform_user else None

    if master:
        return (
            master.email_host_user or settings.EMAIL_HOST_USER,
            master.email_host_password or settings.EMAIL_HOST_PASSWORD,
        )

    return (
        settings.EMAIL_HOST_USER,
        settings.EMAIL_HOST_PASSWORD,
    )
```

Update internal sending logic to use these helpers.

Do not change public function signatures.

---

# 16. Login and Account Status

## 16.1 Helper

```python
def can_login(user):
    if user.is_platform_user:
        return True

    master = user.effective_master()
    return master is not None and master.status == Account.Status.ACTIVE
```

## 16.2 Login Behavior

If login is blocked by status, show clear message:

```text
Your account is suspended or inactive. Please contact support.
```

Do not show generic authentication failure.

Platform users bypass this check.

Sub-users are blocked automatically when their master is inactive.

---

# 17. Subscription Placeholder

## 17.1 Purpose

The Subscription model is a future-proofing table. It is not enforced yet.

Future billing systems can write to it.

## 17.2 Model

```python
class Subscription(models.Model):
    master_account = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscription",
    )

    plan_name = models.CharField(max_length=100, default="Trial")

    status = models.CharField(
        max_length=20,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.TRIAL,
    )

    started_at = models.DateTimeField(auto_now_add=True)
    ends_at = models.DateTimeField(null=True, blank=True)
```

```python
class SubscriptionStatus(models.TextChoices):
    TRIAL = "TRIAL", "Trial"
    ACTIVE = "ACTIVE", "Active"
    EXPIRED = "EXPIRED", "Expired"
    CANCELLED = "CANCELLED", "Cancelled"
```

## 17.3 Creation Rule

Create default Trial subscription for every new master account.

Backfill existing master accounts.

Do not enforce subscription status yet.

---

# 18. Master Self-Service Portal

This is Phase 2.

## 18.1 Access

Allowed:

- master accounts
- platform users viewing on behalf of a selected master

Denied:

- sub-users by default

## 18.2 Dashboard

Show:

- accessible properties
- accessible units
- active leases
- unpaid invoices
- total outstanding balance
- recent payments
- subscription status
- account status
- support unread count

No write access from dashboard.

## 18.3 Master Invoices

Read-only.

Views:

```python
master_invoices_view(request, master_account_id=None)
master_invoice_detail(request, pk, master_account_id=None)
```

Requirements:

- scoped by existing scoping helpers
- no creation/edit/delete
- no status changes
- detail view must reject out-of-scope invoice

Filters:

- property
- unit
- tenant
- status
- date range
- unpaid only

## 18.4 Master Payments

Read-only.

Views:

```python
master_payments_view(request, master_account_id=None)
master_payment_detail(request, pk, master_account_id=None)
```

Requirements:

- scoped by existing scoping helpers
- no payment creation/edit/delete
- no allocation changes
- detail view must reject out-of-scope payment

Filters:

- property
- unit
- tenant
- payment date range
- method
- search/reference

---

# 19. Support Messaging

This is Phase 2.

## 19.1 Model

```python
class SupportMessage(models.Model):
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sent_support_messages",
    )

    master_account = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="support_messages",
    )

    body = models.TextField()

    is_from_staff = models.BooleanField(default=False)

    read_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
```

Add indexes for:

- `master_account`
- `created_at`
- `read_at`

## 19.2 Master View

Create:

```python
master_support_thread(request)
```

Rules:

- master account sees own thread only
- POST creates message with `master_account=request.user`
- `is_from_staff=False`
- mark staff messages read when master opens thread

## 19.3 Staff Inbox

Add permission:

```python
accounts.respond_to_support
```

Views:

```python
staff_support_inbox(request)
staff_support_thread(request, master_account_id)
```

Staff inbox shows:

- master account
- unread count
- last message preview
- last message time
- status
- subscription status

Staff replies:

- `sender=request.user`
- `master_account=selected_master`
- `is_from_staff=True`

No WhatsApp/email notification in v1.

---

# 20. WhatsApp Password Reset for Sub-users

This is Phase 2.

## 20.1 Reset Request

Create:

```python
subuser_whatsapp_reset_request(request, pk)
```

Allowed:

- owning master account
- platform users

Denied:

- unrelated masters
- sub-users
- anonymous users

Behavior:

1. Validate target is a sub-user.
2. Validate actor can manage target.
3. Validate target has WhatsApp/phone field.
4. Generate token using Django’s password reset token tools.
5. Send reset link via WhatsApp.
6. Use `get_whatsapp_config_for_user(sub_user.master_account)`.
7. Audit action.

Do not hand-roll tokens.

Use:

- `default_token_generator`
- `urlsafe_base64_encode`
- `force_bytes`

## 20.2 Reset Confirm

Create:

```python
subuser_whatsapp_reset_confirm(request, uidb64, token)
```

Use Django token validation pattern.

Requirements:

- validate uid
- validate token
- show set password form
- use Django password validators
- save via `set_password`
- show success

## 20.3 Direct Password Change

Create:

```python
subuser_change_password(request, pk)
```

Allowed:

- owning master account
- platform users

Denied:

- unrelated masters
- sub-users

Form:

- new password
- confirm password
- Django password validation

Audit every password change.

---

# 21. Navigation Strategy

## 21.1 Platform Staff

Add links for:

- Master Accounts
- Licensing
- Audit Log
- Support Inbox
- View Master Portal

## 21.2 Master Accounts

Add links for:

- Dashboard
- Invoices
- Payments
- Support
- Sub-users

## 21.3 Sub-users

Sub-users should only see links for views they are allowed to access.

Do not show master portal links to sub-users in Phase 2.

---

# 22. Security Requirements

## 22.1 Server-Side Enforcement

Every sensitive action must validate server-side:

- current user role
- account tier
- ownership
- scope
- permission

## 22.2 Avoid Trusting POST Data

Never trust posted:

- master account id
- property ids
- unit ids
- managed properties
- role ids
- support thread master id

Validate every id against allowed querysets.

## 22.3 No Template-Only Security

Templates may hide buttons, but views must enforce access.

## 22.4 Audit Sensitive Actions

Audit all changes affecting:

- account access
- roles
- licenses
- property ownership
- password resets
- support staff replies if practical

---

# 23. Migration Strategy

## 23.1 Production Safety

Migrations must be safe on existing data.

Do not require manual database edits.

## 23.2 Backfill Rules

- Existing staff/superusers remain platform users.
- Existing non-staff users become master accounts by logic.
- Existing non-platform users receive `unit_quota = total current units`.
- Existing non-platform users receive all units in `licensed_units`.
- Existing properties keep `master_account=None`.
- Existing master accounts receive Trial subscription rows.

## 23.3 Deployment Order

Recommended deployment order:

1. Deploy model fields and migrations.
2. Deploy helper methods.
3. Deploy queryset scoping.
4. Deploy role model and seed roles.
5. Deploy licensing screens.
6. Deploy login status check.
7. Deploy audit log.
8. Deploy subscription placeholder.
9. Run tests.
10. Review production access manually.

---

# 24. Testing Plan

## 24.1 Phase 1 Tests

Required:

1. Platform user sees all accessible units/properties.
2. Master under quota gets all owned units automatically.
3. Master over quota uses explicit `licensed_units`.
4. Sub-user with managed properties sees only allowed units.
5. Sub-user with no managed properties sees all master-accessible units.
6. License edit over quota fails with no partial save.
7. License edit rejects unit from another master.
8. Property transfer removes old master licensed units.
9. Property transfer removes old master sub-user managed properties.
10. Property transfer does not change Lease, Invoice, Payment, Tenant records.
11. Master deletion with sub-users is blocked by PROTECT.
12. Suspended master cannot log in.
13. Suspended master’s sub-user cannot log in.
14. Platform user bypasses status login check.
15. Subscription row is created for new master.

## 24.2 Phase 2 Tests

Required:

1. Master dashboard excludes out-of-scope records.
2. Platform user can view selected master portal.
3. Sub-user cannot access master dashboard.
4. Master invoice list is scoped.
5. Master invoice detail rejects out-of-scope invoice.
6. Master payment list is scoped.
7. Master payment detail rejects out-of-scope payment.
8. Master can send support message.
9. Staff with permission can reply.
10. Staff without support permission is denied.
11. Crafted POST cannot post to another master’s thread.
12. Master can send WhatsApp reset only for own sub-user.
13. Unrelated master cannot reset another sub-user.
14. Platform user can reset any sub-user.
15. Reset confirm accepts valid token.
16. Reset confirm rejects invalid token.
17. Direct password change works for owning master.
18. Direct password change denied for unrelated master.
19. Password reset/change creates audit log.

---

# 25. Acceptance Criteria

The implementation is acceptable when:

- Existing production data migrates without manual fixes.
- Existing platform/staff access still works.
- Existing `has_perm()` checks continue working.
- Master accounts cannot see another master’s records.
- Sub-users cannot escape their master’s scope.
- Queryset scoping is centralized and reused.
- Licensing under quota requires no manual selection.
- Licensing over quota requires explicit unit selection.
- Account status blocks inactive customers and sub-users.
- Property transfer does not alter child business records.
- Audit log records sensitive actions.
- Subscription placeholder exists and backfills correctly.
- Phase 2 master portal is read-only and scoped.
- Support messages are isolated per master.
- WhatsApp password reset uses Django token logic.

---

# 26. Non-Goals

Do not implement yet unless separately requested:

- payment gateway integration
- automatic subscription billing
- automatic account suspension by payment status
- feature plan enforcement
- public API
- WhatsApp notification for every support message
- master invoice editing
- master payment editing
- sub-user master portal access
- full CRM/support ticketing system

---

# 27. Future Roadmap

## 27.1 Subscription Enforcement

Future model expansion:

```text
Plan
    unit_limit
    user_limit
    storage_limit
    whatsapp_limit
    feature flags
```

Subscription status can later drive:

- account status
- quota
- feature access
- billing reminders

## 27.2 Billing Integration

Future payment gateway integration may update:

- Subscription.status
- Account.status
- unit_quota
- invoice/payment records for SaaS billing

## 27.3 Branding

Master accounts may later need:

- logo
- invoice header
- receipt footer
- WhatsApp signature
- email footer
- primary color
- PDF branding

## 27.4 Credential Profiles

Instead of storing credentials directly on Account long-term, introduce:

```text
CredentialProfile
    WhatsApp provider
    sender number
    SMTP settings
    OAuth provider
    fallback settings
```

## 27.5 Organization Layer

Future large customers may need:

```text
Organization
    Region
    Branch
    Portfolio
    Property
```

For now, master account directly owns properties.

---

# 28. Codex Implementation Guidance

When giving this to Codex, implement in batches.

## Batch 1

- Account fields
- Property master_account
- migrations
- tier helpers
- accessible_units/properties
- scoping helpers

## Batch 2

- queryset scoping across apps
- role model
- role seed migration
- user access updates

## Batch 3

- licensing screens
- property transfer
- audit log
- account status login check
- subscription placeholder

## Batch 4

- master portal
- support messages
- WhatsApp password reset

Each batch should run tests before moving to the next batch.

---

# 29. Final Notes

This architecture intentionally avoids a large rewrite. It adds SaaS-grade tenancy on top of the existing TMS structure by using Account, Property, and Unit as the core isolation boundary.

The most important implementation rule is simple:

> No user should ever see or act on a record unless that record is reachable through their allowed Property/Unit scope.
