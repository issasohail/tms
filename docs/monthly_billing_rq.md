# Monthly Billing RQ Worker

Local monthly billing background jobs use RQ with Redis.

Start Redis locally first. The default URL is:

```text
redis://localhost:6379/1
```

Then start the billing worker:

```bash
python manage.py billing_rq_worker
```

The Billing Control Center enqueues long actions through RQ and polls:

```text
/invoices/monthly-billing/jobs/<job_id>/status.json
```

For local regression tests when MySQL cannot create the test database, use:

```bash
python manage.py test invoices --settings=tms.test_settings
```

This uses SQLite and disables historical migrations so tests build from current models.
