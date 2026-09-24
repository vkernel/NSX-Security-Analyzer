#!/bin/sh
# Fresh local installation from prebuilt Docker Hub images (macOS/Linux/WSL).
set -eu
umask 077

command -v docker >/dev/null 2>&1 || { echo 'Install Docker Desktop or Docker Engine with Compose first.' >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo 'Start Docker and try again.' >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo 'Docker Compose v2 is required.' >&2; exit 1; }
[ -t 0 ] || { echo 'Download this script, then run it from an interactive terminal to create your administrator.' >&2; exit 1; }

destination=${1:-nsx-security-analyzer}
if [ -e "$destination" ]; then
  echo "Destination already exists: $destination. Use the documented upgrade steps for existing installations." >&2
  exit 1
fi
# Do not replace a running source-based or previous installation of this product.
if [ -n "$(docker ps -aq --filter label=com.docker.compose.project=nsx-security-analyzer)" ] || \
   [ -n "$(docker volume ls -q --filter label=com.docker.compose.project=nsx-security-analyzer)" ]; then
  echo 'An NSX Security Analyzer Compose installation already exists. Use its upgrade instructions.' >&2
  exit 1
fi
mkdir -p "$destination"
destination=$(cd "$destination" && pwd)
script_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -f "$script_directory/compose.yaml" ]; then
  cp "$script_directory/compose.yaml" "$destination/compose.yaml"
else
  command -v curl >/dev/null 2>&1 || { echo 'curl is required to download the deployment file.' >&2; exit 1; }
  curl --fail --show-error --location https://raw.githubusercontent.com/vkernel/NSX-Security-Analyzer/main/deploy/compose.yaml -o "$destination/compose.yaml"
fi
cd "$destination"
echo 'Downloading the application image and generating private installation secrets…'
docker pull vkernel/nsx-security-analyzer:06e589c
# Generate secrets in a temporary container; no host Python installation is needed.
docker run --rm --network none --entrypoint python vkernel/nsx-security-analyzer:06e589c -c 'import secrets; print("DJANGO_SECRET_KEY="+secrets.token_hex(32)); print("POSTGRES_PASSWORD="+secrets.token_hex(32)); print("WEB_PORT=8000")' > .env
chmod 600 .env
docker compose pull
docker compose up -d
echo 'Waiting for the application (up to three minutes)…'
ready=0
for attempt in $(seq 1 90); do
  if docker compose exec -T web python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/', timeout=2)" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
if [ "$ready" -ne 1 ]; then
  echo "Application did not become ready. Your data and secrets are preserved in $destination." >&2
  echo 'Run: docker compose logs --tail=100 db migrate web worker scheduler' >&2
  exit 1
fi
echo 'Create the administrator account below. Password input is hidden.'
docker compose exec web python manage.py createsuperuser
echo "Installation complete: http://localhost:8000"
echo "Deployment directory: $destination"
echo 'Keep .env with your database backups. Manage this stack with docker compose in that directory.'
