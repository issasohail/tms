# Scheduled Billing and Late-Fee Deployment

These files are prepared locally only. Do not install them until the local changes have been reviewed, committed, pushed, and pulled on production.

## Local verification

```powershell
cd E:\tenant_management_system
.\.venv\Scripts\Activate.ps1
python manage.py check
python manage.py migrate
python manage.py makemigrations --check
python manage.py send_late_fee_reminders --dry-run
python manage.py run_scheduled_billing --dry-run
python manage.py test
```

Monthly scheduling is configured under TMS Settings, in **Billing Scale & Locale**:

- Automatic Monthly Billing
- Monthly Billing Day (1-28, default 2)
- Monthly Billing Time (Pakistan time, default 09:05)

Late-fee automatic scheduling remains controlled by **Send reminders automatically**. The Invoice List **Run Due Late Fees** action is a manual fallback and processes only reminders already due.
Set **Late Fee Reminder Time** in the same settings panel (Pakistan time, default 09:00).
Set **Automation start date** before enabling the timer. Batch runs exclude invoices
due before that date, preventing historical overdue invoices from being messaged or
charged when automation is first enabled.

The systemd timers wake every five minutes. Their services use ``--scheduled`` and
only run the billing or late-fee workflow during the corresponding configured
Pakistan-time window. Changing either time in Settings therefore does not require
editing or reinstalling the timer files.

## Production application deployment

First inspect the live service configuration; reuse its exact user and environment setup if it differs from the checked-in unit files:

```bash
systemctl cat tms-kirayas.service
systemctl cat tms-billing-worker.service
```

Then deploy the application through the normal reviewed Git flow:

```bash
cd /home/ivs/apps/tms
source .venv/bin/activate
git pull origin main
python manage.py check
python manage.py migrate
python manage.py collectstatic --noinput
```

Restart only the service names confirmed by the preceding `systemctl cat` commands. Known candidates are:

```bash
sudo systemctl restart tms-kirayas.service
sudo systemctl restart tms-billing-worker.service
```

Verify the web UI and run both schedulers without side effects before installing timers:

```bash
python manage.py send_late_fee_reminders --dry-run
python manage.py run_scheduled_billing --dry-run
python manage.py send_late_fee_reminders --list-excluded
```

The invoice list now links to **Late Fee Control & Log**. Use that screen to review
the exact due invoices before sending, temporarily hold an invoice (which blocks
both its reminder and reminder-based fee), resume a hold, approve pending fees,
and inspect the post-send log. Zero-amount invoices are always excluded. The
General & Billing settings also provide **Skip invoices issued this month** and an
optional WhatsApp summary to the configured Accounts staff member.

## Install the late-fee timer

```bash
sudo cp deployment/systemd/tms-late-fee-reminders.service /etc/systemd/system/
sudo cp deployment/systemd/tms-late-fee-reminders.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tms-late-fee-reminders.timer
sudo systemctl status tms-late-fee-reminders.timer --no-pager
systemctl list-timers --all | grep late-fee
```

## Install the monthly billing timer

```bash
sudo cp deployment/systemd/tms-scheduled-billing.service /etc/systemd/system/
sudo cp deployment/systemd/tms-scheduled-billing.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tms-scheduled-billing.timer
sudo systemctl status tms-scheduled-billing.timer --no-pager
systemctl list-timers --all | grep scheduled-billing
```

## Logs and ongoing verification

```bash
journalctl -u tms-late-fee-reminders.service -n 100 --no-pager
journalctl -u tms-late-fee-reminders.service --since today
journalctl -u tms-scheduled-billing.service -n 100 --no-pager
journalctl -u tms-scheduled-billing.service --since today
systemctl status tms-late-fee-reminders.timer --no-pager
systemctl status tms-scheduled-billing.timer --no-pager
systemctl list-timers --all | grep tms-
```

No new secrets are required. The unit files read the existing `/home/ivs/apps/tms/.env`; confirm that path against the live Django and billing-worker units before installation.

Do not enable `METER_ENABLE_AUTOMATIC_CUTOFF` or populate `METER_CREDIT_ALLOWED_METER_IDS` as part of this deployment. Physical relay testing remains a separate, one-meter allowlisted production procedure.
