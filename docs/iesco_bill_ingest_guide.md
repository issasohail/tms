# IESCO Bill Fetch and Ingest

The local fetcher is `invoices/iesco_bill_fetch.py`. The repository-root
`iesco_bill_fetch.py` remains as a compatibility launcher for an existing
Windows Task Scheduler command.

The canonical endpoint path is:

```text
/invoices/api/iesco-bill/ingest/
```

The legacy `/tms/` mount is also available at:

```text
/tms/invoices/api/iesco-bill/ingest/
```

Use the canonical domain-root URL unless the deployment is explicitly
configured with the legacy prefix.

## Local Windows fetch

Set the endpoint and API key in the Task Scheduler action environment or in a
wrapper PowerShell script. Do not put the key in the Python source.

```powershell
Set-Location E:\tenant_management_system
$env:TMS_IESCO_INGEST_URL = "https://YOUR-DOMAIN/invoices/api/iesco-bill/ingest/"
$env:TMS_IESCO_API_KEY = "THE-SAME-VALUE-CONFIGURED-IN-DJANGO-SETTINGS"
python -m invoices.iesco_bill_fetch 17146151548911
```

The fetcher stores PITC's bill-history rows. `current_month_paid` is retained
for compatibility with the supplied script, but specifically describes the
most recent completed month in PITC's history—not the newly displayed bill.
Its value is inferred by comparing that row's payment and bill amounts.

## Direct PowerShell endpoint test

```powershell
$headers = @{ "X-API-Key" = "THE-SAME-VALUE-CONFIGURED-IN-DJANGO-SETTINGS" }
$body = @{
    reference_no = "17146151548911"
    fetched_at = "2026-09-07T10:15:30+00:00"
    bill_month = "AUG 26"
    due_date = "17 SEP 26"
    grand_total = "3682"
    bill_history = @(
        @{ month = "Jul26"; units = "27"; bill = "3682"; payment = "6874"; paid = $true }
    )
    current_month_paid = $true
} | ConvertTo-Json -Depth 5

Invoke-RestMethod `
    -Method Post `
    -Uri "https://YOUR-DOMAIN/invoices/api/iesco-bill/ingest/" `
    -Headers $headers `
    -ContentType "application/json" `
    -Body $body
```

Readings appear in Django admin under **Invoices > Iesco bill readings**. The
admin list is read-only and shows the inferred payment status, amount, due
date, first-received time, and last-updated time.
