#!/bin/bash
# VPS2-native backup: pg_dump + git push to cct-db-backups
# Runs locally — no DB credentials exposed to GitHub

set -e
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="/srv/db-backups/alejandro_db"
mkdir -p "$BACKUP_DIR"

cd "$BACKUP_DIR"

# Get DB URL from .env (locally readable, password is never stored elsewhere)
DATABASE_URL=$(grep '^DATABASE_URL=' /srv/rag-engine/.env | cut -d= -f2-)

echo "[$(date)] Starting backup..."

# Custom dump — password is in .env, this runs on VPS2 only
pg_dump "$DATABASE_URL" --no-owner --no-acl -Fc > "alejandro_db_${TIMESTAMP}.dump"
pg_dump "$DATABASE_URL" --no-owner --no-acl --schema-only -f "alejandro_db_${TIMESTAMP}-schema.sql"

echo "  Dump: $(du -sh alejandro_db_${TIMESTAMP}.dump | cut -f1)"
echo "  Schema: alejandro_db_${TIMESTAMP}-schema.sql"

# Keep last 30
ls -1t *.dump | tail -n +31 | xargs -r rm --
ls -1t *.sql | tail -n +31 | xargs -r rm --

# Git push
git add .
git commit -m "backup: alejandro_db $(date +%Y-%m-%d)" --quiet || echo "  No changes"
git push origin master --quiet 2>&1 || echo "  Git push issue (first time? run git push manually once)"

echo "[$(date)] Backup complete"
