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

Late-fee automatic scheduling remains controlled by **Send reminders automatically**. The Invoice List **Run Due Late Fees** action is a manual fallback and processes only reminders already due.

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
```

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
