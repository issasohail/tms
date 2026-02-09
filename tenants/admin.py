from django.contrib import admin
from django import forms
from django.utils.html import format_html
from django.urls import reverse, path
from django.shortcuts import render, get_object_or_404
from .models import Tenant
from image_cropping import ImageCroppingMixin
from django.urls import path
from django.http import JsonResponse


class TenantAdminForm(forms.ModelForm):
    class Meta:
        model = Tenant
        fields = '__all__'
        widgets = {
            'address': forms.Textarea(attrs={'rows': 3, 'class': 'vLargeTextField'}),
            'emergency_contact_phone': forms.TextInput(attrs={'placeholder': '+1234567890'}),
            'first_name': forms.TextInput(attrs={'class': 'vTextField'}),
            'last_name': forms.TextInput(attrs={'class': 'vTextField'}),
        }


@admin.register(Tenant)
class TenantAdmin(ImageCroppingMixin, admin.ModelAdmin):
    form = TenantAdminForm

    list_display = (
        'action_links',
        'full_name',
        'contact_info',
        'current_property',
        'status_badge',
        'document_thumbnails'
    )
    list_filter = ('is_active', 'gender',
                   'leases__unit__property__property_name')
    search_fields = ('first_name', 'last_name', 'email', 'phone', 'cnic')
    list_per_page = 20
    actions = ['print_tenant_info', 'activate_selected', 'deactivate_selected']

    readonly_fields = (
        'photo_preview',
        'cnic_front_preview',
        'cnic_back_preview',
        # combined_cnic_preview',
        'created_at',
        'updated_at',
        'print_button',
    )

    fieldsets = [
        ('Personal', {'fields': ['photo_preview', 'photo_crop', 'photo']}),
        ('CNIC Front', {'fields': [
         'cnic_front_preview', 'cnic_front_crop', 'cnic_front']}),
        ('CNIC Back', {'fields': [
         'cnic_back_preview', 'cnic_back_crop', 'cnic_back']}),

        ('Personal Information', {
            'fields': (
                # 'photo_preview', 'photo',
                ('first_name', 'last_name'),
                ('email', 'phone'),
                ('gender', 'date_of_birth'),
                'address',
                'print_button',
            )
        }),

        ('Identification Documents', {
            'fields': (
                'cnic',
                # ('cnic_front', 'cnic_back'),
                # ('cnic_front_preview', 'cnic_back_preview'),
                # combined_cnic_preview',
            )
        }),
        ('Family Information', {
            'fields': (
                ('number_of_family_member', 'emergency_contact_relation'),
                ('emergency_contact_name', 'emergency_contact_phone'),
            ),
        }),
        ('Status & Notes', {
            'fields': (
                ('is_active', 'created_at'),
                ('updated_at',),
                'notes'
            )
        }),
    ]

    # ========== List Display Helpers ==========

    @admin.display(description='Full Name')
    def full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}"

    @admin.display(description='Contact Info')
    def contact_info(self, obj):
        return format_html(
            "{}<br>{}<br>CNIC: {}",
            obj.email,
            obj.phone,
            obj.cnic or "Not provided"
        )

    @admin.display(description='Current Property')
    def current_property(self, obj):
        active_lease = obj.leases.filter(status='active').first()
        return active_lease.unit.property.property_name if active_lease else "No active lease"

    @admin.display(description='Documents')
    def document_thumbnails(self, obj):
        parts = []
        for field in ['photo', 'cnic_front', 'cnic_back']:
            image = getattr(obj, field)
            if image:
                parts.append(
                    f'<a href="{image.url}" target="_blank">'
                    f'<img src="{image.url}" width="30" style="margin-right:5px;border:1px solid #ddd;"/></a>'
                )
        return format_html(''.join(parts)) if parts else "-"

    @admin.display(description='Status', boolean=True)
    def status_badge(self, obj):
        return obj.is_active

    # ========== Form View Helpers ==========

    @admin.display(description='Photo Preview')
    def photo_preview(self, obj):
        if obj.photo:
            return format_html(
                '<div style="display: flex; align-items: center;">'
                '  <img src="{}" style="max-width: 150px; max-height: 150px; margin-right: 10px; border: 1px solid #ccc;" />'
                '  <div>'
                '    <button type="submit" name="_rotate_photo_left" style="margin:2px;">↺ Rotate Left</button><br>'
                '    <button type="submit" name="_rotate_photo_right" style="margin:2px;">↻ Rotate Right</button>'
                '  </div>'
                '</div>',
                obj.photo.url
            )
            # '<img src="{}" style="max-width:150px; max-height:150px; border:1px solid #ddd;"/>', obj.photo.url)
        return "No photo available"

    @admin.display(description='CNIC Front Preview')
    def cnic_front_preview(self, obj):
        if obj.cnic_front:
            return format_html(
                '<div style="display: flex; align-items: center;">'
                '  <img src="{}" style="max-width: 150px; max-height: 150px; margin-right: 10px; border: 1px solid #ccc;" />'
                '  <div>'
                '    <button type="submit" name="_rotate_photo_left" style="margin:2px;">↺ Rotate Left</button><br>'
                '    <button type="submit" name="_rotate_photo_right" style="margin:2px;">↻ Rotate Right</button>'
                '  </div>'
                '</div>',
                obj.photo.url
            )
            # '<img src="{}" style="max-width:150px; max-height:150px; border:1px solid #ddd;"/>', obj.cnic_front.url)
        return "-"

    @admin.display(description='CNIC Back Preview')
    def cnic_back_preview(self, obj):
        if obj.cnic_back:
            return format_html(
                '<div style="display: flex; align-items: center;">'
                '  <img src="{}" style="max-width: 150px; max-height: 150px; margin-right: 10px; border: 1px solid #ccc;" />'
                '  <div>'
                '    <button type="submit" name="_rotate_photo_left" style="margin:2px;">↺ Rotate Left</button><br>'
                '    <button type="submit" name="_rotate_photo_right" style="margin:2px;">↻ Rotate Right</button>'
                '  </div>'
                '</div>',
                obj.photo.url
            )
            # '<img src="{}" style="max-width:150px; max-height:150px; border:1px solid #ddd;"/>', obj.cnic_back.url)
        return "-"

    @admin.display(description='Combined CNIC Preview')
    def combined_cnic_preview(self, obj):
        docs = []
        if obj.cnic_front:
            docs.append(
                f'<div style="float:left;width:48%;margin-right:2%;">'
                f'<h4>Front</h4><img src="{obj.cnic_front.url}" style="max-width:150px; max-height:100px; border:1px solid #ccc;"/></div>'
            )
        if obj.cnic_back:
            docs.append(
                f'<div style="float:left;width:48%;margin-left:2%;">'
                f'<h4>Back</h4><img src="{obj.cnic_back.url}" style="max-width:150px; max-height:100px; border:1px solid #ccc;"/></div>'
            )
        if docs:
            docs.append('<div style="clear:both;"></div>')
            return format_html(''.join(docs))
        return "No CNIC documents uploaded"

    @admin.display(description="Print Info")
    def print_button(self, obj):
        url = reverse('admin:tenants_tenant-print', args=[obj.pk])
        return format_html('<a class="button" href="{}" target="_blank">🖨️ Print Tenant Info</a>', url)

    # ========== Action Links ==========

    @admin.display(description='Actions')
    def action_links(self, obj):
        change_url = reverse('admin:tenants_tenant_change', args=[obj.pk])
        delete_url = reverse('admin:tenants_tenant_delete', args=[obj.pk])
        return format_html(
            '<a href="{}" class="button" title="Edit"><i class="fa fa-edit"></i></a> '
            '<a href="{}" class="button" title="Delete"><i class="fa fa-trash"></i></a>',
            change_url, delete_url
        )

    # ========== Custom URLs and Actions ==========

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('<int:tenant_id>/print/',
                 self.admin_site.admin_view(self.print_view), name='tenants_tenant-print'),
            path('<int:pk>/rotate/<str:field>/<str:direction>/',
                 self.admin_site.admin_view(self.rotate_ajax), name='tenant_rotate'),
        ]
        return custom_urls + urls

    def print_view(self, request, tenant_id):
        tenant = get_object_or_404(Tenant, pk=tenant_id)
        return render(request, 'admin/print_tenant.html', {'tenant': tenant})

    @admin.action(description='Print selected tenants')
    def print_tenant_info(self, request, queryset):
        return render(request, 'admin/print_selected_tenants.html', {'tenants': queryset})

    # ========== Photo Rotation ==========

    def change_view(self, request, object_id, form_url='', extra_context=None):
        return super().change_view(request, object_id, form_url, {'show_rotation_buttons': True})

    def response_change(self, request, obj):
        for key, method in [
            ("_rotate_photo_left", obj.rotate_photo_left),
            ("_rotate_photo_right", obj.rotate_photo_right),
            ("_rotate_cnic_front_left", obj.rotate_cnic_front_left),
            ("_rotate_cnic_front_right", obj.rotate_cnic_front_right),
            ("_rotate_cnic_back_left", obj.rotate_cnic_back_left),
            ("_rotate_cnic_back_right", obj.rotate_cnic_back_right),
        ]:
            if key in request.POST:
                method()
        return super().response_change(request, obj)

        def _render_preview(self, obj, field):
            img = getattr(obj, field)
            if not img:
                return "No image"
            url = img.url
            return format_html(
                '''
                <div style="display:flex; align-items:center;">
                <img src="{}" style="max-width:150px; max-height:150px; border:1px solid #ccc;"/>
                <div style="display:flex; flex-direction:column; margin-left:10px;">
                    <button class="rotate btn btn-sm btn-primary" data-field="{}" data-dir="left">↺ Rotate Left</button>
                    <button class="rotate btn btn-sm btn-primary" data-field="{}" data-dir="right">↻ Rotate Right</button>
                </div>
                </div>
            ''', url, field, field
            )

    def rotate_ajax(self, request, pk, field, direction):
        obj = get_object_or_404(Tenant, pk=pk)
        degrees = 90 if direction == 'left' else -90
        success = obj.rotate_image(field, degrees)
        return JsonResponse({'success': success})

    class Media:
        css = {'all': [
            'https://cdn.jsdelivr.net/npm/bootstrap@4.6/dist/css/bootstrap.min.css']}
        js = ['https://code.jquery.com/jquery-3.6.0.min.js',
              'admin/js/tenant-rotate.js']
