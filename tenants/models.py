from django.db import models
from django.core.validators import MinValueValidator
from django.db.models import Sum
from PIL import Image
from io import BytesIO
from django.core.files.base import ContentFile
import os
from django.utils.text import slugify
from image_cropping import ImageRatioField
from django.utils import timezone
from django.apps import apps
import re
from django.core.exceptions import ValidationError
from django.db import models

CNIC_DIGITS = re.compile(r'\D+')


def normalize_cnic(value: str) -> str:
    return CNIC_DIGITS.sub('', value or '')


def tenant_photo_upload_to(instance, filename):
    # Get file extension
    ext = filename.split('.')[-1]
    # Create filename: cnic#-tenantname.ext
    filename = f"{instance.cnic}-{slugify(instance.first_name + ' ' + instance.last_name)}-photo.{ext}"
    return os.path.join('tenants/photos/', filename)


def cnic_front_upload_to(instance, filename):
    # Get file extension
    ext = filename.split('.')[-1]
    # Create filename: cnic#-front.ext
    filename = f"{instance.cnic}-{slugify(instance.first_name + ' ' + instance.last_name)}-CNICfront.{ext}"

    return os.path.join('tenants/cnic/', filename)


def cnic_back_upload_to(instance, filename):
    # Get file extension
    ext = filename.split('.')[-1]
    # Create filename: cnic#-back.ext
    filename = f"{instance.cnic}-{slugify(instance.first_name + ' ' + instance.last_name)}-CNICback.{ext}"
    return os.path.join('tenants/cnic/', filename)


class Tenant(models.Model):
    GENDER_CHOICES = [
        ('M', 'Male'),
        ('F', 'Female'),
        ('O', 'Other'),
    ]
    prefix = models.CharField(
        max_length=10, null=True, blank=True, default="Mr.")
    first_name = models.CharField(max_length=50)
    relation = models.CharField(
        max_length=10, null=True, blank=True, default="S/O.")
    last_name = models.CharField(max_length=50)
    email = models.EmailField(null=True, blank=True)
    phone = models.CharField(max_length=20, null=True, blank=True)
    phone2 = models.CharField(max_length=20, null=True, blank=True)
    phone3 = models.CharField(max_length=20, null=True, blank=True)
    cnic = models.CharField(max_length=15)
    # NEW: normalized digits-only shadow field
    cnic_digits = models.CharField(
        max_length=13, blank=True, null=True, unique=True, editable=False, db_index=True)
    address = models.TextField(
        blank=True, null=True, default='Rawalpindi,Pakistan')
    gender = models.CharField(
        max_length=1, choices=GENDER_CHOICES, default='M', blank=True, null=True)
    date_of_birth = models.DateTimeField(blank=True, null=True)
    emergency_contact_name = models.CharField(
        max_length=100, null=True, blank=True)
    emergency_contact_phone = models.CharField(
        max_length=20, null=True, blank=True)
    emergency_contact_relation = models.CharField(
        max_length=20, null=True, blank=True)
    number_of_family_member = models.CharField(max_length=2, default=4)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True, null=True, default="")
    photo = models.ImageField(
        upload_to=tenant_photo_upload_to, blank=True, null=True)
    photo_crop = ImageRatioField('photo', '300x300', size_warning=True)
    cnic_front = models.ImageField(
        upload_to=cnic_front_upload_to, blank=True, null=True)
    cnic_front_crop = ImageRatioField('photo', '300x300', size_warning=True)
    cnic_back = models.ImageField(
        upload_to=cnic_back_upload_to, blank=True, null=True)
    cnic_back_crop = ImageRatioField('photo', '300x300', size_warning=True)

    def __str__(self):
        return f"{self.first_name} {self.last_name}"

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}"

    def get_full_name_agreement(self):
        return f"{self.first_name} {self.relation} {self.last_name}"

    def clean(self):
        super().clean()
        if self.cnic:
            digits = normalize_cnic(self.cnic)
            if len(digits) != 13:
                raise ValidationError(
                    {'cnic': 'CNIC must contain exactly 13 digits.'})

    @property
    def current_lease(self):
        """Safely get the most recent active lease"""
        try:
            if hasattr(self, 'active_leases'):
                return self.active_leases[0] if self.active_leases else None

            return self.leases.filter(
                status='active',
                start_date__lte=timezone.now().date(),
                end_date__gte=timezone.now().date()
            ).order_by('-start_date').first()
        except Exception:
            return None

    @property
    def property_name(self):
        """Safe property name access"""
        lease = self.current_lease
        if lease and hasattr(lease, 'unit') and lease.unit and hasattr(lease.unit, 'property'):
            return lease.unit.property.property_name
        return None

    @property
    def unit_number(self):
        """Safe unit number access"""
        lease = self.current_lease
        if lease and hasattr(lease, 'unit') and lease.unit:
            return lease.unit.unit_number
        return None

    @property
    def total_payment(self):
        return (self.monthly_rent or 0) + (self.society_maintenance or 0)

    def save(self, *args, **kwargs):
        # Get the current instance from database (if it exists)
        if self.pk:
            old_instance = Tenant.objects.get(pk=self.pk)

            # Check and delete old photo if it exists and is being changed
            if old_instance.photo and old_instance.photo != self.photo:
                if os.path.isfile(old_instance.photo.path):
                    os.remove(old_instance.photo.path)

            # Check and delete old cnic_front if it exists and is being changed
            if old_instance.cnic_front and old_instance.cnic_front != self.cnic_front:
                if os.path.isfile(old_instance.cnic_front.path):
                    os.remove(old_instance.cnic_front.path)

            # Check and delete old cnic_back if it exists and is being changed
            if old_instance.cnic_back and old_instance.cnic_back != self.cnic_back:
                if os.path.isfile(old_instance.cnic_back.path):
                    os.remove(old_instance.cnic_back.path)
        self.cnic_digits = normalize_cnic(self.cnic) or None
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # Delete files when the tenant is deleted
        if self.photo and os.path.isfile(self.photo.path):
            os.remove(self.photo.path)
        if self.cnic_front and os.path.isfile(self.cnic_front.path):
            os.remove(self.cnic_front.path)
        if self.cnic_back and os.path.isfile(self.cnic_back.path):
            os.remove(self.cnic_back.path)

        super().delete(*args, **kwargs)

    @property
    def current_lease(self):
        """Get the active lease for this tenant"""
        try:
            Lease = apps.get_model('leases', 'Lease')
            return self.leases.filter(status='active').latest('start_date')
        except Lease.DoesNotExist:
            return None

    @property
    def balance(self):
        from leases.models import Lease
        from invoices.models import Invoice
        from payments.models import Payment

        total_invoiced = Invoice.objects.filter(
            lease__tenant=self
        ).aggregate(total=Sum('total_amount'))['total'] or 0

        total_paid = Payment.objects.filter(
            lease__tenant=self
        ).aggregate(total=Sum('amount'))['total'] or 0

        return total_invoiced - total_paid

    def rotate_photo_left(self):
        self._rotate_image('photo', -90)

    def rotate_photo_right(self):
        self._rotate_image('photo', 90)

    def rotate_cnic_front_left(self):
        self._rotate_image('cnic_front', -90)

    def rotate_cnic_front_right(self):
        self._rotate_image('cnic_front', 90)

    def rotate_cnic_back_left(self):
        self._rotate_image('cnic_back', -90)

    def rotate_cnic_back_right(self):
        self._rotate_image('cnic_back', 90)

    def _rotate_image(self, field_name, degrees):
        image_field = getattr(self, field_name)
        if not image_field:
            return

        # Open the image
        img = Image.open(image_field)

        # Rotate the image
        rotated_img = img.rotate(degrees, expand=True)

        # Save the rotated image back to the field
        buffer = BytesIO()
        ext = os.path.splitext(image_field.name)[1].lower()

        # Preserve the original format (JPEG, PNG, etc.)
        if img.format == 'PNG':
            rotated_img.save(buffer, format='PNG')
            ext = 'png'
        else:
            rotated_img.save(buffer, format='JPEG', quality=95)
            ext = 'jpg'

        buffer.seek(0)

        # Close the image before deleting the file
        img.close()
        rotated_img.close()

        # Generate filename
        filename = os.path.basename(image_field.name)
        name, _ = os.path.splitext(filename)
        new_filename = f"{name}.{ext}"

        # Delete the old file
        if os.path.isfile(image_field.path):
            os.remove(image_field.path)

        # Save the new file
        base = os.path.basename(image_field.name)
        rotated_name = f"{os.path.splitext(base)[0]}_rot{ext}"
        image_field.save(rotated_name, ContentFile(buffer.read()), save=False)
        self.save()
        return True

    class Meta:
        ordering = ['last_name', 'first_name']
