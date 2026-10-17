# Backups

## What the archive contains

`data/backend/backups/network-control-YYYYMMDD-HHMMSS.tar.gz`:

| File | Contents |
|---|---|
| `manifest.json` | Format version, date, row counts per table |
| `database.json` | All PostgreSQL tables |
| `openbao.snap` | OpenBao raft snapshot (encrypted with the unseal key) |

ClickHouse flows are not included — they are analytics data with a limited retention period.

## Creating a backup

- Automatically once a day (if enabled in settings); the last `backup_keep` backups are kept.
- Manually — **Backups → Create backup** or `docker compose exec backend python -m app.cli backup`.
- Download — the button in the list (recorded in the audit log).

Keep `openbao/keys/unseal.key`, the OpenBao root token shown by the setup wizard, `data/backend/bootstrap.json`, `data/backend/approle/` and `.env` separately.

## Restore

1. Deploy the project, put back `.env`, `openbao/keys/unseal.key`, `data/backend/bootstrap.json` and `data/backend/approle/`.
2. Start `postgres` and `openbao`: `docker compose up -d postgres openbao`.
3. Restore OpenBao:

   ```bash
   tar xzf network-control-*.tar.gz openbao.snap
   docker cp openbao.snap nc-openbao:/tmp/
   docker exec -e BAO_ADDR=http://127.0.0.1:8200 \
     -e BAO_TOKEN=<the root token shown by the setup wizard> \
     nc-openbao bao operator raft snapshot restore -force /tmp/openbao.snap
   ```

4. Restore the database (tables are truncated and refilled from the archive):

   ```bash
   cp network-control-*.tar.gz data/backend/backups/
   docker compose run --rm backend python -m app.cli restore-db /data/backups/network-control-YYYYMMDD-HHMMSS.tar.gz
   ```

5. Start the remaining services: `docker compose up -d`.
