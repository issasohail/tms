from __future__ import annotations

from django.conf import settings
from redis import Redis
from rq import Queue


def get_billing_queue():
    redis_url = getattr(settings, "BILLING_RQ_REDIS_URL", "redis://localhost:6379/1")
    queue_name = getattr(settings, "BILLING_RQ_QUEUE", "billing")
    connection = Redis.from_url(redis_url)
    return Queue(queue_name, connection=connection)


def enqueue_billing_job(progress_job):
    queue = get_billing_queue()
    rq_job = queue.enqueue(
        "invoices.tasks.run_billing_progress_job",
        progress_job.pk,
        job_timeout="2h",
        result_ttl=86400,
        failure_ttl=86400,
    )
    progress_job.rq_job_id = rq_job.id
    progress_job.save(update_fields=["rq_job_id", "updated_at"])
    return rq_job
