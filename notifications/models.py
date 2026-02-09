from django.db import models
from tenants.models import Tenant
from django.contrib.auth import get_user_model
from django.conf import settings
from django.db import models


class Notification(models.Model):
    NOTIFICATION_TYPES = [
        ('system', 'System Notification'),
        ('email', 'Email'),
        ('sms', 'SMS'),
        ('call', 'Phone Call'),
        ('meeting', 'In-Person Meeting'),
        ('message', 'Direct Message'),  # Added this new type
    ]

    CATEGORIES = [
        ('payment', 'Payment'),
        ('lease', 'Lease'),
        ('maintenance', 'Maintenance'),
        ('general', 'General'),
    ]
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
                             on_delete=models.CASCADE, related_name="notifications", null=True, blank=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    subject = models.CharField(max_length=200)
    message = models.TextField()
    notification_type = models.CharField(
        max_length=20, choices=NOTIFICATION_TYPES)
    category = models.CharField(
        max_length=20, choices=CATEGORIES, default='general')
    is_read = models.BooleanField(default=False)
    attachments = models.FileField(
        upload_to='notifications/attachments/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications_created",
    )
    is_reply = models.BooleanField(default=False)  # For message threading
    parent_message = models.ForeignKey('self', on_delete=models.SET_NULL,
                                       null=True, blank=True)

    def __str__(self):
        return f"{self.subject} - {self.tenant}"

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Notification'
        verbose_name_plural = 'Notification'
