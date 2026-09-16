# IESCO helper pairing

## Staff guide

1. On a Windows computer using a normal Pakistani internet connection, open the IESCO dashboard through its HTTPS address and click **Helper**.
2. Under **Step 1: Download and install**, click **Download IESCO Helper Setup**. Double-click the downloaded `TMS IESCO Fetch Helper Setup.exe` once. Windows confirms installation.
3. Under **Step 2: Connect and fetch all**, click **Connect This Computer & Fetch All**. Allow the browser to open TMS IESCO Fetch Helper. The dashboard shows the paired computer and immediately tracks Fetch All progress. Pairing from a local HTTP page is blocked with a clear HTTPS message.
4. Later, click **Fetch** for one meter or **Fetch All** without reconnecting. The progress panel shows processed, updated, and failed counts. When complete, the bill list refreshes through AJAX. The helper checks the active reference list, refuses to contact PITC if an active VPN adapter is detected, sends parsed bills to TMS for review, and exits.
5. Review, verify, and confirm fetched readings in TMS as usual. Fetching does not create invoices.

If the browser does not open the helper, run the downloaded setup EXE again and retry pairing. Pairing requests expire after 10 minutes. A revoked device must be paired again by an authorised staff user.
Staff who installed helper 2.0 must download and run the new setup, then reconnect once to enable automatic fetching and live progress. A server-side pairing record alone cannot prove the Windows program is still installed; if the browser cannot open it, reinstall and reconnect.

## Developer build on Windows

The build computer needs the repository, Python virtual environment, requests, BeautifulSoup, and PyInstaller. The EXE contains the Python runtime and bill parser; staff computers need none of these. The helper uses the HTTPS base URL embedded at build time, and never accepts a server URL from a browser link.

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\tools\build_iesco_helper_exe.ps1 -TmsBaseUrl 'https://kirayas.com/tms'
```

Use the actual production HTTPS base URL if it differs. The output is `tools/dist/TMS IESCO Fetch Helper Setup.exe`. The generated build config contains only this public URL; it contains no API key. Rebuild after changing the helper source or target domain. The EXE is user-installed under the current Windows account and registers `tms-iesco://` under HKCU. The device credential is encrypted with Windows DPAPI for that account.

## Production deployment

1. Back up the production database and the current application release using the normal deployment procedure.
2. If legacy API-key jobs still run, configure `IESCO_BILL_API_KEY` in the existing production secret environment before this release, and update those jobs with the same key. The key is no longer a literal in Django settings. Rotate the formerly source-controlled key through the normal secret-change procedure. No key is needed for the new EXE.
3. Deploy the changed Django files, migrations through `invoices/migrations/0037_iesco_helper_fetch_run.py`, and the built EXE at `tools/dist/TMS IESCO Fetch Helper Setup.exe` in the application directory. Do not deploy the build folder, build config, or any local device credential.
4. In the deployed Python environment run `python manage.py migrate invoices` and `python manage.py check`. Restart the existing application process using the normal deployment procedure. The pair exchange requires Django to see HTTPS through the existing reverse proxy. No nginx, systemd, or Docker files were changed here.
5. Confirm the download is available to an authorised user. On a Pakistani Windows computer, install, pair, fetch one active test reference, and confirm the saved reading is **Parsed** and no invoice changed.
6. In Django admin, use **Iesco helper devices** to inspect last use and revoke or delete a device. Use **Iesco helper pairings** to audit requests and create a new pairing request.

The old global API-key ingest and active-reference endpoints remain for existing legacy jobs. The new EXE never uses that key. Devices use only the new pairing, reference, and ingest endpoints.
Server-side PITC fetching is disabled in Django, so a CSV import or old server Fetch URL cannot make Contabo contact PITC.

## Tests

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test invoices.test_iesco_helper invoices.test_iesco_ingest.IescoBillIngestTests --settings=tms.test_settings --noinput
```

## Rollback

If deployment fails, revoke any newly paired devices, restore the previous application release and prior EXE download, and restart the existing application process. Leave the new tables in place during immediate rollback; they do not alter existing bill or invoice data. After a database backup and when no new release uses pairing data, a deliberate database rollback may reverse migration 0036 with `python manage.py migrate invoices 0035`; that removes device and pairing records. Existing legacy API-key jobs continue with the previous release.
