from django.db import models

# Create your models here.
from django.db import models
from django.core.validators import MinValueValidator


class Utility(models.Model):
    UTILITY_TYPES = (
        ('water', 'Water'),
        ('electricity', 'Electricity'),
        ('gas', 'Gas'),
        ('maintenance', 'Maintenance'),
        ('other', 'Other'),
    )

    DISTRIBUTION_METHODS = (
        ('equal', 'Equal Distribution'),
        ('per_person', 'Per Person'),
        ('usage', 'Based on Usage'),
    )

    property = models.ForeignKey(
        'properties.Property', on_delete=models.CASCADE, related_name='utilities')
    utility_type = models.CharField(max_length=20, choices=UTILITY_TYPES)
    amount = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    billing_date = models.DateField()
    due_date = models.DateField()
    distribution_method = models.CharField(
        max_length=20, choices=DISTRIBUTION_METHODS)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.get_utility_type_display()} - {self.property.property_name} - {self.amount}"

    class Meta:
        verbose_name_plural = "Utilities"
        ordering = ['-billing_date']
