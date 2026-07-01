from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .forms_late_fee import LeaseLateFeeSettingsForm
from .models import Lease
from .models_late_fee import LeaseLateFeeSettings


@login_required
def lease_late_fee_settings(request, pk):
    lease = get_object_or_404(Lease, pk=pk)
    instance, _ = LeaseLateFeeSettings.objects.get_or_create(lease=lease)

    if request.method == "POST":
        form = LeaseLateFeeSettingsForm(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            messages.success(request, "Late fee settings updated for this lease.")
            return redirect("leases:lease_detail", pk=lease.pk)
    else:
        form = LeaseLateFeeSettingsForm(instance=instance)

    return render(request, "leases/lease_late_fee_settings.html", {
        "lease": lease,
        "form": form,
    })
