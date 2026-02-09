from django.db import models
from django.core.validators import MinValueValidator

from django.db import models


class ExpenseDistribution(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


class Property(models.Model):
    PROPERTY_TYPES = (
        ('apartment', 'Apartment'),
        ('house', 'House'),
        ('condo', 'Condo'),
        ('commercial', 'Commercial'),
    )

    property_name = models.CharField(
        max_length=100, verbose_name='Property Name', db_column='name')
    owner_prefix = models.CharField(
        max_length=5, null=True, blank=True, default="Mr.")
    owner_name = models.CharField(max_length=100)
    owner_father_name = models.CharField(max_length=100, blank=True, null=True)
    relation = models.CharField(
        max_length=10, null=True, blank=True, default="S/O")
    owner_phone = models.CharField(max_length=20, blank=True, null=True)
    owner_address = models.CharField(max_length=200, blank=True, null=True)
    owner_cnic = models.CharField(max_length=15)
    owner_phone = models.CharField(max_length=25, blank=True, null=True)
    caretaker_prefix = models.CharField(
        max_length=5, null=True, blank=True, default="Mr.")
    caretaker_prefix = models.CharField(
        max_length=5, null=True, blank=True, default="Mr.")
    caretaker_name = models.CharField(max_length=100, blank=True, null=True)
    caretaker_father_name = models.CharField(
        max_length=100, blank=True, null=True)
    caretaker_relation = models.CharField(
        max_length=10, null=True, blank=True, default="S/O")
    caretaker_address = models.CharField(max_length=200, blank=True, null=True)
    caretaker_cnic = models.CharField(max_length=15, blank=True, null=True)
    caretaker_phone = models.CharField(max_length=25, blank=True, null=True)
    property_address1 = models.CharField(max_length=200, blank=True, null=True)
    property_address2 = models.TextField(max_length=100, blank=True, null=True)
    property_city = models.CharField(max_length=20, blank=True, null=True)
    property_state = models.CharField(max_length=10, blank=True, null=True)
    property_zipcode = models.CharField(max_length=10, blank=True, null=True)

    type = models.CharField(max_length=50)      # with exactly these names
    property_type = models.CharField(max_length=20, choices=PROPERTY_TYPES)
    total_units = models.PositiveIntegerField()
    description = models.CharField(max_length=1000, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.property_name} "

    def full_address(self):
        parts = [self.property_address1]

        if self.property_address2.strip():  # Add only if not blank or whitespace
            parts.append(self.property_address2)

        parts.append(self.property_city)
        parts.append(self.property_state)
        parts.append(self.property_zipcode)

        return ", ".join(parts)

    class Meta:
        ordering = ['property_name']
        verbose_name_plural = "Properties"


class Unit(models.Model):
    UNIT_STATUS = [
        ('vacant', 'Vacant'),
        ('occupied', 'Occupied'),
        ('maintenance', 'Maintenance'),
    ]

    property = models.ForeignKey(
        'Property', on_delete=models.CASCADE, related_name='units')
    unit_number = models.CharField(max_length=20)
    electric_meter_num = models.CharField(max_length=20,
                                          null=True, blank=True, default="0000000000")
    is_smart_meter = models.BooleanField(default=False)
    gas_meter_num = models.CharField(
        max_length=20, null=True, blank=True, default="12345")
    society_maintenance = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, default="1200.00")
    water_charges = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, default="6000.00")
    monthly_rent = models.DecimalField(
        # Add this if missing
        decimal_places=2, default="25000.00", max_digits=10)
    security_requires = models.CharField(
        max_length=20, null=True, blank=True, default="Two Months")
    ceiling_fan = models.IntegerField(blank=True, null=True, default=3)
    exhaust_fan = models.IntegerField(blank=True, null=True, default=3)
    ceiling_lights = models.IntegerField(blank=True, null=True, default=16)
    stove = models.IntegerField(blank=True, null=True, default=0)
    keys = models.IntegerField(blank=True, null=True, default=2)
    paint_condition = models.CharField(
        max_length=100, null=True, blank=True, default="New Paint with no marks or water seapage")
    wardrobes = models.IntegerField(blank=True, null=True, default=2)
    bedrooms = models.IntegerField(blank=True, null=True, default=2)
    bathrooms = models.IntegerField(blank=True, null=True, default=2)
    kitchens = models.IntegerField(blank=True, null=True, default=1)
    hall = models.IntegerField(blank=True, null=True, default=1)
    square_footage = models.IntegerField(null=True, blank=True)
    comments = models.CharField(
        max_length=100, null=True, blank=True, default="Good Condition.")
    status = models.CharField(
        max_length=20, choices=UNIT_STATUS, default='vacant')  # Add this if missing

    def __str__(self):
        return f"{self.property.property_name}-{self.unit_number}"
