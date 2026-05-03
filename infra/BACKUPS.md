# NewSoft backups

`newsoft-backup.timer` runs nightly at 03:30 and writes custom-format Postgres dumps to `/var/backups/newsoft/newsoft-YYYYMMDD-HHMM.pgc`. Files older than 14 days are deleted by the service after a successful run.

Restore by stopping NewSoft, choosing a backup file, and running:

```bash
sudo systemctl stop newsoft-orchestrator newsoft-dashboard
sudo -u postgres dropdb --if-exists newsoft
sudo -u postgres createdb -O newsoft newsoft
sudo -u postgres pg_restore -d newsoft /var/backups/newsoft/<file>.pgc
sudo systemctl start newsoft-orchestrator newsoft-dashboard
```
