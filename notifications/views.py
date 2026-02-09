from django.shortcuts import render, get_object_or_404
from .models import Notification
from django.views.generic import CreateView
from django.urls import reverse_lazy
from .forms import MessageForm
from django.db import models  # Add this import at the top
from django.views.generic import ListView, DeleteView, UpdateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse

from .forms import NotificationForm
from tenants.models import Tenant
from django.contrib import messages


class NotificationCreateView(CreateView):
    model = Notification
    form_class = NotificationForm
    template_name = 'notifications/notification_form.html'

    def get_initial(self):
        initial = super().get_initial()
        if 'tenant_id' in self.request.GET:
            initial['tenant'] = Tenant.objects.get(
                pk=self.request.GET['tenant_id'])
        return initial

    def form_valid(self, form):
        form.instance.notification_type = 'message'
        form.instance.created_by = self.request.user
        messages.success(self.request, 'Message sent successfully!')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('notifications:list')


def notification_list(request):
    return render(request, 'notifications/list.html', {
        'notifications': Notification.objects.filter(user=request.user)
    })


def notification_detail(request, pk):
    notification = get_object_or_404(Notification, pk=pk, user=request.user)
    notification.is_read = True
    notification.save()
    return render(request, 'notifications/detail.html', {
        'notification': notification
    })


class CreateMessageView(CreateView):
    model = Notification
    form_class = MessageForm
    template_name = 'notifications/create_message.html'
    success_url = reverse_lazy('notifications:list')

    def form_valid(self, form):
        form.instance.notification_type = 'message'
        form.instance.created_by = self.request.user
        return super().form_valid(form)


def notification_list(request):
    # Show both notifications and messages
    notifications = Notification.objects.filter(
        models.Q(tenant__user=request.user) |
        models.Q(created_by=request.user)
    ).order_by('-created_at')

    return render(request, 'notifications/list.html', {
        'notifications': notifications
    })


class NotificationListView(LoginRequiredMixin, ListView):
    model = Notification
    template_name = 'notifications/list.html'
    context_object_name = 'notifications'
    paginate_by = 10

    def get_queryset(self):
        # Show both notifications and messages for the current user
        return Notification.objects.filter(
            models.Q(tenant__user=self.request.user) |
            models.Q(created_by=self.request.user)
        ).order_by('-created_at')


class NotificationDeleteView(LoginRequiredMixin, SuccessMessageMixin, DeleteView):
    model = Notification
    template_name = 'notifications/notification_confirm_delete.html'
    success_url = reverse_lazy('notifications:list')
    success_message = "Notification deleted successfully."

    def get_queryset(self):
        # Only allow deleting notifications that belong to the current user
        return super().get_queryset().filter(
            models.Q(tenant__user=self.request.user) |
            models.Q(created_by=self.request.user)
        )


def mark_all_as_read(request):
    if request.method == 'POST':
        # Mark all unread notifications as read for the current user
        Notification.objects.filter(
            user=request.user,
            is_read=False
        ).update(is_read=True)
        messages.success(request, 'All notifications marked as read.')
        return redirect('notifications:list')
    return redirect('notifications:list')


class NotificationUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = Notification
    form_class = NotificationForm
    template_name = 'notifications/notification_form.html'
    success_message = "Notification updated successfully."

    def get_success_url(self):
        return reverse('notifications:list')

    def get_queryset(self):
        # Only allow updating notifications created by the current user
        return super().get_queryset().filter(created_by=self.request.user)
