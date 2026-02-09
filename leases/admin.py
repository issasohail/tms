from .models_pcr import PropertyConditionReport, PCRPhoto
from .models import Lease, LeaseAgreementClause, LeaseTemplate
from django.db.models import F
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html, mark_safe
from django.db import models
from django.urls import reverse, NoReverseMatch
from django.conf import settings
from django.db.models import Sum
from dal import autocomplete
from django import forms
from .models import Unit
from properties.models import Property
from leases.utils.agreement_generator import generate_lease_agreement
from django.http import HttpResponse

# Define ClauseInline first

from django.contrib import admin
from .models import Lease, DefaultClause, LeaseAgreementClause


@admin.register(DefaultClause)
class DefaultClauseAdmin(admin.ModelAdmin):
    list_display = ("clause_number", "short_body", "is_active", "updated_at")
    list_editable = ("is_active",)
    ordering = ("clause_number",)
    search_fields = ("body",)

    def short_body(self, obj):
        return (obj.body[:80] + "...") if len(obj.body) > 80 else obj.body
    short_body.short_description = "Body"


class LeaseAgreementClauseInline(admin.TabularInline):
    model = LeaseAgreementClause
    extra = 0
    fields = ("clause_number", "template_text", "is_customized")
    readonly_fields = ()
    ordering = ("clause_number",)
    show_change_link = False
    ordering = ('clause_number',)

    def has_add_permission(self, request, obj=None):
        return False  # Prevent adding new clauses directly in admin

@admin.register(Lease)
class LeaseAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "unit", "start_date", "end_date", "status")
    list_filter = ("status", "start_date", "end_date")
    search_fields = ("tenant__first_name", "tenant__last_name", "unit__unit_number")
    inlines = [LeaseAgreementClauseInline]



    

# Lease Admin Form


class LeaseAdminForm(forms.ModelForm):
    class Meta:
        model = Lease
        fields = '__all__'
        widgets = {
            'unit': autocomplete.ModelSelect2(
                url='unit-autocomplete',
                forward=['property']
            ),
        }

# Admin actions


@admin.action(description='Return security deposit')
def return_security_deposit(modeladmin, request, queryset):
    for lease in queryset:
        if not lease.security_deposit_returned and lease.status == 'ended':
            lease.return_security_deposit(
                notes="Returned via admin action"
            )


@admin.action(description="Bulk update placeholder in clauses")
def bulk_update_placeholder(modeladmin, request, queryset):
    placeholder = request.POST.get('placeholder')
    new_value = request.POST.get('new_value')

    if not placeholder or not new_value:
        return

    queryset.update(
        template_text=F('template_text').replace(
            f'[{placeholder}]',
            new_value
        )
    )


@admin.action(description="Apply template to selected leases")
def apply_template(modeladmin, request, queryset):
    template_id = request.POST.get('template_id')
    if not template_id:
        return

    template = LeaseTemplate.objects.get(id=template_id)
    for lease in queryset:
        lease.update_from_template(template)

# Lease Admin



class LeaseAdmin(admin.ModelAdmin):
    form = LeaseAdminForm
    autocomplete_fields = ['unit']
    inlines = [LeaseAgreementClauseInline]  # Now defined above
    actions = ['generate_agreement',
               return_security_deposit, 'download_lease_agreement']

    list_display = (
        'id', 'action_links', 'is_active',
        'property_link', 'unit_display', 'tenant_display',
        'society_maintenance', 'security_deposit_display',
        'monthly_rent_display', 'rent_increase_percent', 'lease_period', 'current_balance'
    )

    list_editable = ('rent_increase_percent',)

    readonly_fields = ('tenant_photo_preview', 'cnic_preview',
                       'lease_period', 'current_balance', 'monthly_rent_display')
    list_filter = ('unit__property__property_name', 'status')
    search_fields = ('tenant__first_name', 'tenant__last_name',
                     'unit__property__property_name', 'unit__unit_number')
    date_hierarchy = 'start_date'

    fieldsets = (
        (None, {
            'fields': (('tenant', 'unit', 'status'),
                       ('start_date', 'end_date'),
                       ('security_deposit', 'monthly_rent', 'society_maintenance'),
                       'monthly_rent_display',
                       'terms', 'notes')
        }),
        ('Documents', {
            'classes': ('wide', 'extrapretty'),
            'fields': (('tenant_photo_preview', 'cnic_preview'),)
        }),
    )

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)

        # Customize specific fields
        if 'terms' in form.base_fields:
            form.base_fields['terms'].widget = forms.Textarea(
                attrs={'rows': 10, 'cols': 80})
        if 'notes' in form.base_fields:
            form.base_fields['notes'].widget = forms.Textarea(
                attrs={'rows': 5, 'cols': 80})

        return form

    def generate_agreement(self, request, queryset):
        for lease in queryset:
            # Trigger generation for each lease
            # This would actually generate and save the file
            pass

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'unit__property',
            'tenant'
        )

    def get_balance_amount(self, obj):
        """Helper method to get raw balance number without HTML formatting"""
        from invoices.models import Invoice
        from payments.models import Payment

        total_invoiced = Invoice.objects.filter(
            lease=obj
        ).aggregate(total=Sum('amount'))['total'] or 0

        total_paid = Payment.objects.filter(
            lease=obj
        ).aggregate(total=Sum('amount'))['total'] or 0

        return total_invoiced - total_paid

    @admin.display(description='Tenant Photo')
    def tenant_photo_preview(self, obj):
        if obj.tenant and obj.tenant.photo:
            return format_html(
                '<img src="{}{}" style="height:150px;width:150px;object-fit:cover;border-radius:5px;"/>',
                settings.MEDIA_URL, obj.tenant.photo
            )
        return "No photo"

    @admin.display(description='CNIC Documents')
    def cnic_preview(self, obj):
        html = []
        if obj.tenant and obj.tenant.cnic_front:
            html.append(format_html(
                '<div style="float:left;margin-right:15px;"><strong>Front:</strong><br>'
                '<img src="{}{}" style="height:150px;width:200px;border:1px solid #ddd;"/>',
                settings.MEDIA_URL, obj.tenant.cnic_front
            ))

        if obj.tenant and obj.tenant.cnic_back:
            html.append(format_html(
                '<div style="float:left;"><strong>Back:</strong><br>'
                '<img src="{}{}" style="height:150px;width:200px;border:1px solid #ddd;"/>',
                settings.MEDIA_URL, obj.tenant.cnic_back
            ))

        if html:
            html.append('<div style="clear:both;"></div>')
            return mark_safe(''.join(html))
        return "No CNIC documents"

    @admin.display(description='Property')
    def property_link(self, obj):
        if obj.unit and obj.unit.property:
            try:
                url = reverse('admin:properties_property_change',
                              args=[obj.unit.property.id])
                return format_html('<a href="{}">{}</a>', url, obj.unit.property.property_name)
            except NoReverseMatch:
                return obj.unit.property.property_name
        return '-'

    @admin.display(description='Unit')
    def unit_display(self, obj):
        if obj.unit:
            return f"{obj.unit.property.property_name} - {obj.unit.unit_number}"
        return '-'

    @admin.display(description='Tenant')
    def tenant_display(self, obj):
        if obj.tenant:
            return f"{obj.tenant.first_name} {obj.tenant.last_name}"
        return '-'

    @admin.display(description='Monthly Rent')
    def monthly_rent_display(self, obj):
        maintenance = obj.society_maintenance if obj.society_maintenance is not None else 0
        rent = obj.monthly_rent if obj.monthly_rent is not None else 0
        total = rent + maintenance
        return f"Rs.{total:,.2f}" if total else "-"

    @admin.display(description='Security Deposit')
    def security_deposit_display(self, obj):
        return f"Rs.{obj.security_deposit:,.2f}" if obj.security_deposit else '-'

    @admin.display(description='Current Balance')
    def current_balance(self, obj):
        balance = float(self.get_balance_amount(obj))
        return f"Rs.{balance:,.2f}"

    @admin.display(description='Lease Period')
    def lease_period(self, obj):
        return f"{obj.start_date} to {obj.end_date}"

    @admin.display(description='Actions')
    def action_links(self, obj):
        change_url = reverse('admin:leases_lease_change', args=[obj.id])
        delete_url = reverse('admin:leases_lease_delete', args=[obj.id])
        return format_html(
            '<a class="button" href="{}">Edit</a>&nbsp;'
            '<a class="button" href="{}">Delete</a>',
            change_url, delete_url
        )

    def download_lease_agreement(self, request, queryset):
        if len(queryset) != 1:
            self.message_user(
                request, "Please select exactly one lease to generate agreement.", level='error')
            return

        lease = queryset.first()
        try:
            buffer = generate_lease_agreement(lease)

            response = HttpResponse(
                buffer.getvalue(), content_type='application/pdf')
            response[
                'Content-Disposition'] = f'attachment; filename=Lease_Agreement_{lease.id}.pdf'
            return response
        except Exception as e:
            self.message_user(
                request, f"Error generating PDF: {str(e)}", level='error')
    download_lease_agreement.short_description = "Download Lease Agreement"

    @admin.display(description='Active', boolean=True)
    def is_active(self, obj):
        return obj.status == 'active'

    class Media:
        css = {
            'all': ('css/admin-custom.css',)
        }
        js = ('js/admin-custom.js',)

# Lease Template Admin


@admin.register(LeaseTemplate)
class LeaseTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_default', 'created_at', 'updated_at')
    actions = ['set_as_default']

    def set_as_default(self, request, queryset):
        if queryset.count() == 1:
            LeaseTemplate.objects.filter(
                is_default=True).update(is_default=False)
            queryset.update(is_default=True)
    set_as_default.short_description = "Set as default template"


# leases/admin.py


class PCRPhotoInline(admin.TabularInline):
    model = PCRPhoto
    extra = 0
    fields = ("thumbnail", "room", "comment", "taken_at", "order")
    readonly_fields = ("thumbnail", "taken_at")


@admin.register(PropertyConditionReport)
class PCRAdmin(admin.ModelAdmin):
    list_display = ("lease", "title", "created_at", "locked")
    inlines = [PCRPhotoInline]
