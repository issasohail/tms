from django.db import models
from django.utils import timezone
from django.urls import reverse
from django.conf import settings


class FinancialReport(models.Model):
    report_type = models.CharField(max_length=50, choices=[
        ('monthly', 'Monthly'),
        ('quarterly', 'Quarterly'),
        ('annual', 'Annual'),
        ('custom', 'Custom Date Range'),
    ])
    start_date = models.DateField()
    end_date = models.DateField()
    generated_at = models.DateTimeField(default=timezone.now)
    report_file = models.FileField(
        upload_to='financial_reports/', blank=True, null=True)
    notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.get_report_type_display()} Report ({self.start_date} to {self.end_date})"


class Report(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    title = models.CharField(max_length=200)
    content = models.TextField(blank=True)  # Make sure this field exists
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending'
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,

        related_name="reports_created",
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"

    def get_absolute_url(self):
        return reverse('reports:report_detail', args=[str(self.id)])
