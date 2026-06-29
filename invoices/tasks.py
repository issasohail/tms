from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.utils import timezone

from invoices.models import BillingProgressJob, MonthlyBillingRunItem
from invoices.services import (
    generate_monthly_billing_electric,
    generate_monthly_billing_invoices,
    generate_monthly_billing_pdfs,
    prepare_monthly_billing_ready,
    rollback_monthly_billing_run,
    run_monthly_billing_full,
    run_monthly_billing_preflight,
    send_monthly_billing_ready,
)


def _name(obj, attr):
    value = getattr(obj, attr, "")
    return str(value or "")


def _elapsed(job):
    if not job.started_at:
        return 0
    return max(0, int((timezone.now() - job.started_at).total_seconds()))


def _update_progress(job, *, item=None, index=0, total=0, step="", message=""):
    elapsed = _elapsed(job)
    average = Decimal("0.00")
    remaining = 0
    if index:
        average = (Decimal(elapsed) / Decimal(index)).quantize(Decimal("0.01"))
    if total and index and average:
        remaining = int(max(Decimal("0.00"), Decimal(total - index) * average))

    job.current_step = step or job.current_step
    job.current_index = index or job.current_index
    job.total_count = total or job.total_count
    job.elapsed_seconds = elapsed
    job.average_seconds = average
    job.estimated_remaining_seconds = remaining
    job.message = message or job.message
    if item:
        job.current_tenant = " ".join(
            filter(None, [_name(item.tenant, "first_name"), _name(item.tenant, "last_name")])
        ).strip()
        job.current_property = _name(item.property, "property_name")
        job.current_unit = _name(item.unit, "unit_number")
    job.save(update_fields=[
        "current_step",
        "current_index",
        "total_count",
        "elapsed_seconds",
        "average_seconds",
        "estimated_remaining_seconds",
        "message",
        "current_tenant",
        "current_property",
        "current_unit",
        "updated_at",
    ])


def _callback(job):
    def inner(*, item, index, total, step):
        _update_progress(job, item=item, index=index, total=total, step=step)
    return inner


def run_billing_progress_job(progress_job_id):
    close_old_connections()
    job = BillingProgressJob.objects.select_related("billing_run", "created_by").get(pk=progress_job_id)
    run = job.billing_run
    user = job.created_by
    job.status = BillingProgressJob.STATUS_RUNNING
    job.started_at = timezone.now()
    job.current_step = "Starting"
    job.message = ""
    job.error_text = ""
    job.save(update_fields=["status", "started_at", "current_step", "message", "error_text", "updated_at"])

    try:
        progress = _callback(job)
        if job.action == BillingProgressJob.ACTION_RUN_BILLING:
            run_monthly_billing_full(run, created_by=user, progress_callback=progress)
        elif job.action == BillingProgressJob.ACTION_PREFLIGHT:
            run_monthly_billing_preflight(run.billing_month, created_by=user, progress_callback=progress)
        elif job.action == BillingProgressJob.ACTION_RECURRING:
            generate_monthly_billing_invoices(run, progress_callback=progress)
        elif job.action == BillingProgressJob.ACTION_ELECTRIC:
            generate_monthly_billing_electric(run, progress_callback=progress)
        elif job.action == BillingProgressJob.ACTION_READY:
            prepare_monthly_billing_ready(run, progress_callback=progress)
        elif job.action == BillingProgressJob.ACTION_PDFS:
            generate_monthly_billing_pdfs(run, progress_callback=progress)
        elif job.action == BillingProgressJob.ACTION_SEND:
            send_monthly_billing_ready(run, created_by=user, progress_callback=progress)
        elif job.action == BillingProgressJob.ACTION_RETRY:
            send_monthly_billing_ready(run, created_by=user, retry_failed=True, progress_callback=progress)
        elif job.action == BillingProgressJob.ACTION_ROLLBACK:
            blocked = rollback_monthly_billing_run(run, user=user, progress_callback=progress)
            job.result = {"blocked": blocked}
        else:
            raise ValueError(f"Unknown billing job action: {job.action}")

        job.status = BillingProgressJob.STATUS_COMPLETED
        job.completed_at = timezone.now()
        job.current_step = "Completed"
        job.elapsed_seconds = _elapsed(job)
        job.message = "Completed"
        if not job.result:
            job.result = {
                "ready": run.ready_to_send_count,
                "pending": run.pending_attention_count,
                "sent": run.sent_count,
                "failed": run.failed_count,
            }
        job.save(update_fields=[
            "status",
            "completed_at",
            "current_step",
            "elapsed_seconds",
            "message",
            "result",
            "updated_at",
        ])
    except Exception as exc:
        job.status = BillingProgressJob.STATUS_FAILED
        job.completed_at = timezone.now()
        job.current_step = "Failed"
        job.error_text = str(exc)
        job.elapsed_seconds = _elapsed(job)
        job.save(update_fields=["status", "completed_at", "current_step", "error_text", "elapsed_seconds", "updated_at"])
        raise
    finally:
        close_old_connections()
