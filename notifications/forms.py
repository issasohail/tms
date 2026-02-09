from django import forms
from .models import Notification


class MessageForm(forms.ModelForm):
    class Meta:
        model = Notification
        fields = ['tenant', 'subject', 'message', 'attachments']
        widgets = {
            'message': forms.Textarea(attrs={'rows': 5}),
        }


class NotificationForm(forms.ModelForm):
    class Meta:
        model = Notification
        fields = ['tenant', 'subject', 'message', 'attachments']
        widgets = {
            'message': forms.Textarea(attrs={'rows': 4}),
        }
