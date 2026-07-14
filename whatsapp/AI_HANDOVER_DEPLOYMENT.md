# WhatsApp Controlled AI and Staff Handover

The existing Meta Cloud API webhook, outbound service, numbered menus, tenant workflows,
payment approvals, maintenance approvals, media storage and Celery processing remain the
fallback foundation. The controlled AI router can select only registered server-side tools;
tool handlers derive tenant, lease, property, unit and staff identity from the authenticated
sender context.

## Rollout

1. Back up the database and deploy the code.
2. Run `python manage.py migrate`.
3. Configure `WHATSAPP_APP_SECRET` in secure environment storage and restart Django workers.
   Production webhook POSTs fail closed when this secret is missing.
4. In Global Settings, enable **Tenant to staff handover** first and configure routed staff.
5. Enable **AI routing**, then optionally generated responses and multiple tools.
6. If Celery Beat is used, schedule
   `whatsapp.tasks.process_whatsapp_handover_reminders_task` at a frequency no greater than
   the configured reminder interval.
7. Run `python manage.py test whatsapp --settings=tms.test_settings --noinput`.

Calling is deliberately manual. `WhatsAppCallingService` reports calling as unsupported and
records staff call decisions; it does not claim to initiate or forward calls.

## Rollback

Disable AI routing and handover in Global Settings to return immediately to the preserved
hardcoded assistant. For a code rollback, deploy the prior application version. Keep migration
0014 (WhatsApp) and 0015 (core) applied unless a database backup has been restored: reversing
them removes handover tables and the new audit/configuration fields.
