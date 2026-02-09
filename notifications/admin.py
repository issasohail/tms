from django.contrib import admin
from notifications.models import Notification


class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        'get_title',
        'get_recipient',
        'get_notification_type',
        'created_at',
        'is_read'
    )
    list_filter = (
        'notification_type',
        'is_read',
        'created_at'
    )

    def get_title(self, obj):
        return obj.title
    get_title.short_description = 'Title'

    def get_recipient(self, obj):
        return obj.user.username if obj.user else '-'  # Changed from recipient to user
    get_recipient.short_description = 'Recipient'

    def get_notification_type(self, obj):
        return obj.get_notification_type_display()
    get_notification_type.short_description = 'Type'


# Only register once with the admin class you want to use
admin.site.register(Notification, NotificationAdmin)
