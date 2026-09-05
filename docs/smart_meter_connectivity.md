# Smart-meter connectivity

The listener records valid contact from recognised meters in Redis with a TTL and
keeps measurement freshness tied to `LiveReading.ts`. If Redis cannot be reached,
status resolution safely falls back to the existing `LiveReading.ts` rule.

Use the read-only diagnostic report with:

```bash
python manage.py meter_connectivity_report --hours 48
```

For a later production maintenance window, the approved service command should be
reviewed and the verbose `--debug` and `--dump-raw` listener options removed. This
repository change does not modify systemd, nginx, deployment files, or services.
