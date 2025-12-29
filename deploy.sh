set -euo pipefail

git pull

APP_NAME="hyperliquid-trader-watcher"
IMAGE_NAME="${APP_NAME}:latest"

cd "$(dirname "$0")"

if [ ! -f ".env" ]; then
  echo "ERROR: .env not found. Copy .env.example to .env and fill BOT_TOKEN, BOT_ADMINS, etc." >&2
  exit 1
fi

mkdir -p ./data/db ./data/logs

echo "Building docker image: ${IMAGE_NAME}"
docker build -t "${IMAGE_NAME}" .

echo "Stopping previous container (if exists): ${APP_NAME}"
docker rm -f "${APP_NAME}" >/dev/null 2>&1 || true

echo "Creating Docker volume for persistent data (if not exists)"
docker volume create "${APP_NAME}-data" >/dev/null 2>&1 || true

echo "Starting container: ${APP_NAME}"
docker run -d \
  --name "${APP_NAME}" \
  --restart unless-stopped \
  --env-file .env \
  -e DATA_DIR=/data \
  -e DB_PATH=/data/db/app.sqlite3 \
  -e LOG_DIR=/data/logs \
  -v "${APP_NAME}-data:/data" \
  "${IMAGE_NAME}"

echo "Done. Logs: docker logs -f ${APP_NAME}"


