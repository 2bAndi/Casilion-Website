#!/bin/sh
# Daily backup of the database and of data/var (license signing key, release files),
# run on the host by cron (see docs/server-setup.md):
#   20 3 * * * sh /opt/cassilion/docker/backup.sh >> /opt/cassilion/backups/backup.log 2>&1
#
# Keeps 14 daily and 8 weekly (Sunday) copies. The dump is checked with pg_restore --list.
# With BACKUP_RSYNC_TARGET set in .env the folder is copied off the server as well.
set -eu
umask 077  # dumps hold personal data, the archive holds the signing key: owner only
cd "$(dirname "$0")/.."
# Read single values; .env is not shell syntax (e.g. "Name <mail>")
env_get() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | tr -d '\r'; }
POSTGRES_USER=$(env_get POSTGRES_USER)
POSTGRES_DB=$(env_get POSTGRES_DB)
STAMP=$(date +%Y-%m-%d_%H%M)
mkdir -p backups/weekly

DB="backups/db_$STAMP.dump"
docker compose exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom > "$DB.part"
# A dump that pg_restore cannot read is no backup
docker compose exec -T db pg_restore --list < "$DB.part" > /dev/null
mv "$DB.part" "$DB"

VAR="backups/var_$STAMP.tar.gz"
tar -czf "$VAR.part" -C data var
mv "$VAR.part" "$VAR"

if [ "$(date +%u)" = "7" ]; then
  cp "$DB" "$VAR" backups/weekly/
fi

find backups -maxdepth 1 -name 'db_*.dump' -mtime +14 -delete
find backups -maxdepth 1 -name 'var_*.tar.gz' -mtime +14 -delete
find backups/weekly -name '*_*' -mtime +56 -delete

BACKUP_RSYNC_TARGET=$(env_get BACKUP_RSYNC_TARGET || true)
if [ -n "${BACKUP_RSYNC_TARGET:-}" ]; then
  # --delete mirrors the rotation; --max-delete stops a wiped local folder
  # from emptying the off-site copy as well
  rsync -a --delete --max-delete=6 --exclude '*.part' backups/ "$BACKUP_RSYNC_TARGET/"
fi
echo "$STAMP backup ok: $(du -h "$DB" | cut -f1) database, $(du -h "$VAR" | cut -f1) var"
