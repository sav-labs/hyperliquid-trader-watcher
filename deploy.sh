set -euo pipefail

APP_NAME="hyperliquid-trader-watcher"
IMAGE_NAME="${APP_NAME}:latest"

cd "$(dirname "$0")"

echo "Updating code from git..."
git pull

if [ ! -f ".env" ]; then
  echo "ERROR: .env not found. Copy .env.example to .env and fill BOT_TOKEN, BOT_ADMINS, etc." >&2
  exit 1
fi

mkdir -p ./data/db ./data/logs

echo "Removing old image (if exists)..."
docker rmi -f "${IMAGE_NAME}" >/dev/null 2>&1 || true

echo "Building docker image: ${IMAGE_NAME} (no cache, always fresh)"
docker build --no-cache --pull -t "${IMAGE_NAME}" .

echo "Stopping previous container (if exists): ${APP_NAME}"
docker rm -f "${APP_NAME}" >/dev/null 2>&1 || true

echo "Starting container: ${APP_NAME}"
docker run -d \
  --name "${APP_NAME}" \
  --restart unless-stopped \
  --env-file .env \
  -e DATA_DIR=/data \
  -e DB_PATH=/data/db/app.sqlite3 \
  -e LOG_DIR=/data/logs \
  -v "$(pwd)/data:/data" \
  "${IMAGE_NAME}"

echo "Done. Logs: docker logs -f ${APP_NAME}"


