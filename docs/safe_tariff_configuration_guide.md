# Safe Meter Tariff Configuration

## Operator workflow

Before using tariff controls, set and verify each meter's stored **Tariff Capability**. The migration classifies valid numeric DL/T645 addresses beginning with `26` as single-rate, other valid numeric addresses as multi-rate, and missing or invalid identifiers as unknown. Runtime operations use only the saved capability and never infer it again.

To set Rs. 40.0000 for all confirmed meters:

1. Open **Smart Meter > Meters > Bulk Tariffs**.
2. Filter by property, unit, capability, or online state and select the intended meters.
3. Enter `40.0000`, review every selected meter and its capability, and confirm.
4. Leave the result page open while meters run sequentially. Each meter is freshly read, changed only in its tariff fields, written once, immediately read back, and marked Verified only when the read-back matches exactly.
5. Investigate Offline, Failed, Unsafe read-back, or Pending verification results through their audit links. A retry is manual and requires a second confirmation.

For one meter, open its detail page, choose **Configure Tariff**, and first select **Read Current Meter Configuration**. The edit form appears only after a successful live read. Single-rate meters copy the flat value into all four `070115FF` tariff slots. Multi-rate flat mode copies it into all four Rate Set 1 fields in `070104FF` and sets the active rate count to one.

For a future three-rate plan, select Time-of-use, active count `3`, enter labels such as Valley, Flat, and Peak, their prices, and periods covering the full 24-hour day without gaps or overlaps. Save the schedule as a draft. Price configuration uses the verified `070104FF` mapping, but live schedule transmission remains disabled because the repository does not contain a vendor-confirmed byte-level mapping for `070105FF`. Do not enable it from the DI alone.

## Windows PowerShell patch and verification

```powershell
Set-Location E:\tenant_management_system
git apply --check .\TMS_safe_tariff_configuration_2026-09-07.patch
git apply .\TMS_safe_tariff_configuration_2026-09-07.patch
.\.venv\Scripts\Activate.ps1
python manage.py makemigrations smart_meter --check --dry-run
python manage.py migrate
python manage.py test smart_meter --keepdb -v 2
python manage.py check
git diff --check
git add smart_meter templates\includes\meter_filters.html docs\safe_tariff_configuration_guide.md
git commit -m "Add safe smart meter tariff configuration"
git push origin main
```

If the disposable MySQL test database still contains the earlier inconsistent migration state, rebuild that test database through the normal Django test runner before using `--keepdb`. Do not fake migration `maintenance.0006` against that inconsistent schema.

## Production deployment

These commands assume the deployed checkout and service names already documented for TMS. Confirm them with `pwd` and `systemctl status` before restarting.

```bash
cd /home/ivs/apps/tms
git pull origin main
source .venv/bin/activate
python manage.py migrate
python manage.py test smart_meter --keepdb -v 2
python manage.py check
sudo systemctl restart tms-kirayas.service
sudo systemctl restart tms-meter-listener.service
sudo systemctl status tms-kirayas.service tms-meter-listener.service --no-pager
```

Only the web application and meter listener require restart because this change modifies Django code/templates and the existing listener command pipeline. It does not modify nginx, Redis, systemd unit files, or secrets.
