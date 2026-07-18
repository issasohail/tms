import copy

from dal import autocomplete
from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import F, Sum
from django.http import HttpResponse
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html, mark_safe

from leases.utils.agreement_generator import generate_lease_agreement
from leases.utils.move_out_billing import (
    apply_move_out_settlement,
    build_move_out_settlement_preview,
    move_out_billing_trigger,
)

# Define ClauseInline first
from .models import (
    AgreementPlaceholder,
    DefaultClause,
    Lease,
    LeaseAgreementClause,
    LeaseDocument,
    LeaseDocumentCategory,
    LeaseFamilyMember,
    LeaseFileShareLink,
    LeaseRelationshipType,
    LeaseTemplate,
    LeaseUnitOccupancy,
    LeaseVehicle,
    LeaseVehicleType,
    PendingAgreementApproval,
    PendingLeaseVehicleSubmission,
)
from .models_inspections import (
    InspectionAppliance,
    InspectionCategory,
    InspectionDamageCharge,
    InspectionDetail,
    InspectionItem,
    InspectionKey,
    InspectionMeterReading,
    InspectionPhoto,
    InspectionStatus,
    InspectionTemplate,
    InspectionType,
    LeaseInspection,
)
from .models_late_fee import LeaseLateFeeSettings
from .models_pcr import PCRPhoto, PropertyConditionReport
from .models_renewal import LeaseRenewal, LeaseRenewalClause


@admin.register(PendingAgreementApproval)
class PendingAgreementApprovalAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "lease",
        "status",
        "submitted_by",
        "reviewed_by",
        "created_at",
        "reviewed_at",
    )
    list_filter = ("status", "created_at", "reviewed_at")
    search_fields = (
        "lease__tenant__first_name",
        "lease__tenant__last_name",
        "lease__unit__unit_number",
        "proposed_terms",
    )
    raw_id_fields = ("lease", "submitted_by", "reviewed_by")
    readonly_fields = ("created_at", "reviewed_at")


@admin.register(LeaseUnitOccupancy)
class LeaseUnitOccupancyAdmin(admin.ModelAdmin):
    list_display = ("lease", "unit", "move_in_date", "move_out_date", "is_active")
    list_filter = ("move_in_date", "move_out_date", "unit__property__property_name")
    search_fields = (
        "lease__tenant__first_name",
        "lease__tenant__last_name",
        "unit__unit_number",
        "unit__property__property_name",
        "notes",
    )
    raw_id_fields = ("lease", "unit")


@admin.register(LeaseFamilyMember)
class LeaseFamilyMemberAdmin(admin.ModelAdmin):
    list_display = (
        "lease",
        "primary_tenant",
        "family_member",
        "relationship_type",
        "relationship",
        "is_adult",
        "lives_with_tenant",
    )
    list_filter = ("relationship_type", "relationship", "is_adult", "lives_with_tenant")
    search_fields = (
        "primary_tenant__first_name",
        "primary_tenant__last_name",
        "family_member__first_name",
        "family_member__last_name",
        "family_member__cnic",
    )
    raw_id_fields = ("lease", "primary_tenant", "family_member")


@admin.register(LeaseRelationshipType)
class LeaseRelationshipTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active", "sort_order")
    list_editable = ("is_active", "sort_order")
    search_fields = ("name", "code")
    ordering = ("sort_order", "name")


@admin.register(DefaultClause)
class DefaultClauseAdmin(admin.ModelAdmin):
    list_display = ("clause_number", "short_body", "is_active", "updated_at")
    list_editable = ("is_active",)
    ordering = ("clause_number",)
    search_fields = ("body",)

    def short_body(self, obj):
        return (obj.body[:80] + "...") if len(obj.body) > 80 else obj.body

    short_body.short_description = "Body"


@admin.register(AgreementPlaceholder)
class AgreementPlaceholderAdmin(admin.ModelAdmin):
    list_display = (
        "key",
        "label",
        "category",
        "source_type",
        "is_active",
        "sort_order",
    )
    list_editable = ("is_active", "sort_order")
    list_filter = ("is_active", "source_type", "category")
    search_fields = ("key", "label", "description", "resolver_key", "django_path")
    ordering = ("category", "sort_order", "key")


class LeaseAgreementClauseInline(admin.TabularInline):
    model = LeaseAgreementClause
    extra = 0
    fields = ("clause_number", "template_text", "is_customized")
    readonly_fields = ()
    ordering = ("clause_number",)
    show_change_link = False
    ordering = ("clause_number",)

    def has_add_permission(self, request, obj=None):
        return False  # Prevent adding new clauses directly in admin


class LeaseMoveOutAdminForm(forms.ModelForm):
    """Adds a manual final-water-amount field and blocks the save entirely
    if this edit finalizes a move-out (status -> ended/terminated, or
    end_date corrected on an already-ended lease) but the electric meter
    reading doesn't yet cover the new end date."""

    final_water_amount = forms.DecimalField(
        required=False,
        label="Final water charge (move-out only)",
        help_text=(
            "Only used if this save finalizes a move-out for a lease with "
            "water billing enabled and a smart-metered unit. Ignored otherwise."
        ),
    )

    class Meta:
        model = Lease
        fields = "__all__"

    def clean(self):
        cleaned_data = super().clean()
        if not self.instance.pk:
            return cleaned_data  # new lease being created, nothing to move out of

        # Build a lightweight "prospective new state" from cleaned_data
        # without mutating self.instance (Django applies cleaned_data onto
        # the real instance later, in _post_clean()).
        new_state = copy.copy(self.instance)
        for field, value in cleaned_data.items():
            if hasattr(new_state, field):
                setattr(new_state, field, value)

        if move_out_billing_trigger(self.instance, new_state):
            preview = build_move_out_settlement_preview(
                new_state, end_date=new_state.end_date
            )
            if preview["applicable"] and preview["blocked"]:
                raise ValidationError(preview["block_reason"])
        return cleaned_data




class LeaseAdminForm(forms.ModelForm):
    class Meta:
        model = Lease
        fields = "__all__"
        widgets = {
            "unit": autocomplete.ModelSelect2(
                url="unit-autocomplete", forward=["property"]
            ),
        }


# Admin actions


@admin.action(description="Return security deposit")
def return_security_deposit(modeladmin, request, queryset):
    for lease in queryset:
        if not lease.security_deposit_returned and lease.status == "ended":
            lease.return_security_deposit(notes="Returned via admin action")


@admin.action(description="Bulk update placeholder in clauses")
def bulk_update_placeholder(modeladmin, request, queryset):
    placeholder = request.POST.get("placeholder")
    new_value = request.POST.get("new_value")

    if not placeholder or not new_value:
        return

    queryset.update(
        template_text=F("template_text").replace(f"[{placeholder}]", new_value)
    )


@admin.action(description="Apply template to selected leases")
def apply_template(modeladmin, request, queryset):
    template_id = request.POST.get("template_id")
    if not template_id:
        return

    template = LeaseTemplate.objects.get(id=template_id)
    for lease in queryset:
        lease.update_from_template(template)


# Lease Admin

class LeaseVehicleInline(admin.TabularInline):
    model = LeaseVehicle
    extra = 1
    fields = (
        "vehicle_type",
        "registration_number",
        "make",
        "model",
        "color",
        "owner_name",
        "owner_cnic",
        "is_active",
    )

@admin.register(Lease)
class LeaseAdmin(admin.ModelAdmin):
    form = LeaseMoveOutAdminForm
    autocomplete_fields = ["unit"]
    inlines = [
    LeaseAgreementClauseInline,
    LeaseVehicleInline,
]
    actions = [
        "generate_agreement",
        return_security_deposit,
        "download_lease_agreement",
    ]

    list_display = (
        "id",
        "action_links",
        "is_active",
        "property_link",
        "unit_display",
        "tenant_display",
        "society_maintenance",
        "security_deposit_display",
        "monthly_rent_display",
        "rent_increase_percent",
        "lease_period",
        "current_balance",
    )

    list_editable = ("rent_increase_percent",)

    readonly_fields = (
        "tenant_photo_preview",
        "cnic_preview",
        "lease_period",
        "current_balance",
        "monthly_rent_display",
    )
    list_filter = ("unit__property__property_name", "status")
    search_fields = (
        "tenant__first_name",
        "tenant__last_name",
        "unit__property__property_name",
        "unit__unit_number",
    )
    date_hierarchy = "start_date"

    fieldsets = (
        (
            None,
            {
                "fields": (
                    ("tenant", "unit", "status"),
                    ("start_date", "end_date"),
                    ("security_deposit", "monthly_rent", "society_maintenance"),
                    "monthly_rent_display",
                    "terms",
                    "notes",
                )
            },
        ),
        (
            "Documents",
            {
                "classes": ("wide", "extrapretty"),
                "fields": (("tenant_photo_preview", "cnic_preview"),),
            },
        ),
    )

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)

        # Customize specific fields
        if "terms" in form.base_fields:
            form.base_fields["terms"].widget = forms.Textarea(
                attrs={"rows": 10, "cols": 80}
            )
        if "notes" in form.base_fields:
            form.base_fields["notes"].widget = forms.Textarea(
                attrs={"rows": 5, "cols": 80}
            )

        return form

    def generate_agreement(self, request, queryset):
        for lease in queryset:
            # Trigger generation for each lease
            # This would actually generate and save the file
            pass

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("unit__property", "tenant")

    def get_balance_amount(self, obj):
        """Helper method to get raw balance number without HTML formatting"""
        from invoices.models import Invoice
        from payments.models import Payment

        total_invoiced = (
            Invoice.objects.filter(lease=obj).aggregate(total=Sum("amount"))["total"]
            or 0
        )

        total_paid = (
            Payment.objects.filter(lease=obj).aggregate(total=Sum("amount"))["total"]
            or 0
        )

        return total_invoiced - total_paid

    @admin.display(description="Tenant Photo")
    def tenant_photo_preview(self, obj):
        if obj.tenant and obj.tenant.photo:
            return format_html(
                '<img src="{}{}" style="height:150px;width:150px;object-fit:cover;border-radius:5px;"/>',
                settings.MEDIA_URL,
                obj.tenant.photo,
            )
        return "No photo"

    @admin.display(description="CNIC Documents")
    def cnic_preview(self, obj):
        html = []
        if obj.tenant and obj.tenant.cnic_front:
            html.append(
                format_html(
                    '<div style="float:left;margin-right:15px;"><strong>Front:</strong><br>'
                    '<img src="{}{}" style="height:150px;width:200px;border:1px solid #ddd;"/>',
                    settings.MEDIA_URL,
                    obj.tenant.cnic_front,
                )
            )

        if obj.tenant and obj.tenant.cnic_back:
            html.append(
                format_html(
                    '<div style="float:left;"><strong>Back:</strong><br>'
                    '<img src="{}{}" style="height:150px;width:200px;border:1px solid #ddd;"/>',
                    settings.MEDIA_URL,
                    obj.tenant.cnic_back,
                )
            )

        if html:
            html.append('<div style="clear:both;"></div>')
            return mark_safe("".join(html))
        return "No CNIC documents"

    def save_model(self, request, obj, form, change):
        old = Lease.objects.get(pk=obj.pk) if (change and obj.pk) else None
        super().save_model(request, obj, form, change)
        if old is not None and move_out_billing_trigger(old, obj):
            water_amount = form.cleaned_data.get("final_water_amount")
            try:
                result = apply_move_out_settlement(
                    obj, water_amount=water_amount, end_date=obj.end_date
                )
                if result is not None:
                    messages.success(
                        request,
                        "Final electric/water settlement posted for this move-out.",
                    )
            except ValidationError as exc:
                messages.error(request, f"Final settlement not posted: {exc.message}")

    @admin.display(description="Property")
    def property_link(self, obj):
        if obj.unit and obj.unit.property:
            try:
                url = reverse(
                    "admin:properties_property_change", args=[obj.unit.property.id]
                )
                return format_html(
                    '<a href="{}">{}</a>', url, obj.unit.property.property_name
                )
            except NoReverseMatch:
                return obj.unit.property.property_name
        return "-"

    @admin.display(description="Unit")
    def unit_display(self, obj):
        if obj.unit:
            return f"{obj.unit.property.property_name} - {obj.unit.unit_number}"
        return "-"

    @admin.display(description="Tenant")
    def tenant_display(self, obj):
        if obj.tenant:
            return f"{obj.tenant.first_name} {obj.tenant.last_name}"
        return "-"

    @admin.display(description="Monthly Rent")
    def monthly_rent_display(self, obj):
        maintenance = (
            obj.society_maintenance if obj.society_maintenance is not None else 0
        )
        rent = obj.monthly_rent if obj.monthly_rent is not None else 0
        total = rent + maintenance
        return f"Rs.{total:,.2f}" if total else "-"

    @admin.display(description="Security Deposit")
    def security_deposit_display(self, obj):
        return f"Rs.{obj.security_deposit:,.2f}" if obj.security_deposit else "-"

    @admin.display(description="Current Balance")
    def current_balance(self, obj):
        balance = float(self.get_balance_amount(obj))
        return f"Rs.{balance:,.2f}"

    @admin.display(description="Lease Period")
    def lease_period(self, obj):
        return f"{obj.start_date} to {obj.end_date}"

    @admin.display(description="Actions")
    def action_links(self, obj):
        change_url = reverse("admin:leases_lease_change", args=[obj.id])
        delete_url = reverse("admin:leases_lease_delete", args=[obj.id])
        return format_html(
            '<a class="button" href="{}">Edit</a>&nbsp;'
            '<a class="button" href="{}">Delete</a>',
            change_url,
            delete_url,
        )

    def download_lease_agreement(self, request, queryset):
        if len(queryset) != 1:
            self.message_user(
                request,
                "Please select exactly one lease to generate agreement.",
                level="error",
            )
            return

        lease = queryset.first()
        try:
            buffer = generate_lease_agreement(lease)

            response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
            response["Content-Disposition"] = (
                f"attachment; filename=Lease_Agreement_{lease.id}.pdf"
            )
            return response
        except Exception as e:
            self.message_user(request, f"Error generating PDF: {str(e)}", level="error")

    download_lease_agreement.short_description = "Download Lease Agreement"

    @admin.display(description="Active", boolean=True)
    def is_active(self, obj):
        return obj.status == "active"

    class Media:
        css = {"all": ("css/admin-custom.css",)}
        js = ("js/admin-custom.js",)


# Lease Template Admin


@admin.register(LeaseTemplate)
class LeaseTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "is_default", "created_at", "updated_at")
    actions = ["set_as_default"]

    def set_as_default(self, request, queryset):
        if queryset.count() == 1:
            LeaseTemplate.objects.filter(is_default=True).update(is_default=False)
            queryset.update(is_default=True)

    set_as_default.short_description = "Set as default template"


class LeaseRenewalClauseInline(admin.TabularInline):
    model = LeaseRenewalClause
    extra = 0
    fields = ("clause_number", "template_text", "is_customized")
    ordering = ("clause_number",)


@admin.register(LeaseRenewal)
class LeaseRenewalAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "lease",
        "renewal_number",
        "start_date",
        "end_date",
        "monthly_rent",
        "is_agreement_signed",
        "created_by",
        "created_at",
    )
    list_filter = ("is_agreement_signed", "start_date", "end_date")
    search_fields = (
        "lease__tenant__first_name",
        "lease__tenant__last_name",
        "lease__unit__unit_number",
        "notes",
    )
    readonly_fields = ("created_at", "updated_at")
    inlines = [LeaseRenewalClauseInline]


# leases/admin.py


class PCRPhotoInline(admin.TabularInline):
    model = PCRPhoto
    extra = 0
    fields = ("thumbnail", "room", "comment", "taken_at", "sort_order", "order")
    readonly_fields = ("thumbnail", "taken_at")


@admin.register(PropertyConditionReport)
class PCRAdmin(admin.ModelAdmin):
    list_display = ("lease", "title", "created_at", "locked")
    inlines = [PCRPhotoInline]


@admin.register(LeaseDocument)
class LeaseDocumentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "lease",
        "display_name",
        "category",
        "uploaded_by",
        "uploaded_at",
        "is_active",
    )
    list_filter = ("category", "is_active", "uploaded_at")
    search_fields = (
        "display_name",
        "original_filename",
        "description",
        "lease__tenant__first_name",
        "lease__tenant__last_name",
        "lease__unit__unit_number",
    )
    raw_id_fields = ("lease", "lease_history", "uploaded_by")


@admin.register(LeaseDocumentCategory)
class LeaseDocumentCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active", "sort_order")
    list_editable = ("is_active", "sort_order")
    search_fields = ("name", "code")
    ordering = ("sort_order", "name")


@admin.register(LeaseFileShareLink)
class LeaseFileShareLinkAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "lease",
        "document",
        "expires_at",
        "created_by",
        "is_active",
        "created_at",
    )
    list_filter = ("is_active", "expires_at", "created_at")
    search_fields = (
        "token",
        "lease__tenant__first_name",
        "lease__tenant__last_name",
        "document__display_name",
    )
    raw_id_fields = ("lease", "document", "created_by")


@admin.register(InspectionType)
class InspectionTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "display_order", "active")
    list_editable = ("display_order", "active")
    search_fields = ("name",)


@admin.register(InspectionCategory)
class InspectionCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "display_order", "active")
    list_editable = ("display_order", "active")
    search_fields = ("name",)


@admin.register(InspectionStatus)
class InspectionStatusAdmin(admin.ModelAdmin):
    list_display = ("name", "badge_color", "display_order", "active")
    list_editable = ("badge_color", "display_order", "active")
    search_fields = ("name",)


@admin.register(InspectionItem)
class InspectionItemAdmin(admin.ModelAdmin):
    list_display = (
        "category",
        "item_name",
        "display_order",
        "required",
        "allow_photos",
        "allow_damage_cost",
        "allow_notes",
        "active",
    )
    list_filter = (
        "category",
        "required",
        "allow_photos",
        "allow_damage_cost",
        "allow_notes",
        "active",
    )
    list_editable = ("display_order", "active")
    search_fields = ("item_name", "category__name")


@admin.register(InspectionTemplate)
class InspectionTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "display_order", "active", "updated_at")
    list_editable = ("display_order", "active")
    filter_horizontal = ("items",)
    search_fields = ("name", "description")


class InspectionPhotoInline(admin.TabularInline):
    model = InspectionPhoto
    extra = 0


class InspectionDetailInline(admin.TabularInline):
    model = InspectionDetail
    extra = 0
    fields = (
        "category",
        "item_name",
        "status_name",
        "remarks",
        "damage_cost",
        "display_order",
    )
    readonly_fields = ("category", "item_name", "display_order")


class InspectionMeterInline(admin.TabularInline):
    model = InspectionMeterReading
    extra = 0


class InspectionKeyInline(admin.TabularInline):
    model = InspectionKey
    extra = 0


class InspectionApplianceInline(admin.TabularInline):
    model = InspectionAppliance
    extra = 0


class InspectionDamageInline(admin.TabularInline):
    model = InspectionDamageCharge
    extra = 0


@admin.register(LeaseInspection)
class LeaseInspectionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "lease",
        "inspection_type",
        "inspection_date",
        "status",
        "inspector",
        "approved_at",
    )
    list_filter = ("status", "inspection_type", "inspection_date")
    search_fields = (
        "lease__tenant__first_name",
        "lease__tenant__last_name",
        "unit__unit_number",
    )
    raw_id_fields = (
        "lease",
        "property",
        "unit",
        "tenant",
        "inspector",
        "approved_by",
        "created_by",
    )
    inlines = [
        InspectionDetailInline,
        InspectionMeterInline,
        InspectionKeyInline,
        InspectionApplianceInline,
        InspectionDamageInline,
    ]


@admin.register(LeaseLateFeeSettings)
class LeaseLateFeeSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "lease",
        "override_enabled",
        "late_fee_enabled",
        "late_fee_type",
        "late_fee_amount",
        "late_fee_percent",
        "late_fee_grace_days",
        "reminder_interval_days",
        "late_fee_max_reminders",
    )
    list_filter = ("override_enabled", "late_fee_enabled", "late_fee_type")
    search_fields = (
        "lease__tenant__first_name",
        "lease__tenant__last_name",
        "lease__unit__unit_number",
    )
    raw_id_fields = ("lease",)


@admin.register(LeaseVehicleType)
class LeaseVehicleTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active", "sort_order")
    list_editable = ("is_active", "sort_order")
    search_fields = ("name", "code")
    list_filter = ("is_active",)
    ordering = ("sort_order", "name")


@admin.register(LeaseVehicle)
class LeaseVehicleAdmin(admin.ModelAdmin):
    list_display = (
        "lease",
        "tenant",
        "vehicle_type",
        "registration_number",
        "make",
        "model",
        "color",
        "owner_name",
        "is_active",
    )
    list_filter = ("vehicle_type", "is_active")
    search_fields = (
        "registration_number",
        "owner_name",
        "owner_cnic",
        "lease__tenant__first_name",
        "lease__tenant__last_name",
        "lease__unit__unit_number",
        "tenant__first_name",
        "tenant__last_name",
    )


@admin.register(PendingLeaseVehicleSubmission)
class PendingLeaseVehicleSubmissionAdmin(admin.ModelAdmin):
    list_display = (
        "lease",
        "tenant",
        "pending_tenant_submission",
        "vehicle_type",
        "registration_number",
        "owner_name",
        "status",
        "source",
        "submitted_at",
        "reviewed_by",
        "reviewed_at",
    )
    list_filter = ("status", "vehicle_type", "source", "submitted_at")
    search_fields = (
        "registration_number",
        "owner_name",
        "owner_cnic",
        "lease__tenant__first_name",
        "lease__tenant__last_name",
        "tenant__first_name",
        "tenant__last_name",
        "pending_tenant_submission__tenant__first_name",
        "pending_tenant_submission__tenant__last_name",
    )

