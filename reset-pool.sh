#!/bin/bash
# reset-pool.sh — wipe the pool database and restart all services
# Run from the BlackHole directory: bash reset-pool.sh
set -e

COMPOSE_DIR="$(cd "$(dirname "$0")" && pwd)"

# Resolve the Docker volume name: <compose-project-name>_pool_data
# Docker Compose derives the project name from the directory name by default.
PROJECT_NAME="$(basename "$COMPOSE_DIR" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9_-]/_/g')"
VOLUME_NAME="${PROJECT_NAME}_pool_data"
DB_PATH="/var/lib/docker/volumes/${VOLUME_NAME}/_data/pool.db"

echo "Compose dir : $COMPOSE_DIR"
echo "Volume name : $VOLUME_NAME"
echo "Database    : $DB_PATH"
echo ""

echo "[1/3] Stopping pool..."
cd "$COMPOSE_DIR" && docker compose stop blackhole-pool

echo "[2/3] Clearing database..."
if [ -f "$DB_PATH" ]; then
    sqlite3 "$DB_PATH" "
DELETE FROM shares;
DELETE FROM blocks;
DELETE FROM worker_best;
VACUUM;
SELECT 'shares:      ' || COUNT(*) FROM shares;
SELECT 'blocks:      ' || COUNT(*) FROM blocks;
SELECT 'worker_best: ' || COUNT(*) FROM worker_best;
"
else
    echo "  Database not found at $DB_PATH — skipping (will be created fresh on start)."
fi

echo "[3/3] Starting pool + dashboard..."
docker compose up -d blackhole-pool blackhole-dashboard

sleep 5
echo ""
echo "=== Pool logs ==="
docker compose logs --tail=15 blackhole-pool 2>&1 | grep -E "INFO|WARN|ERROR" | head -10
echo ""
echo "Done."
