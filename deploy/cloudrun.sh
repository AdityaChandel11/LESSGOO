#!/usr/bin/env bash
# Deploy SwasthSetu to Google Cloud: Cloud Run (app) + Cloud SQL (managed
# PostgreSQL) + optional Firebase Hosting (CDN and custom domain in front).
#
#   export PROJECT_ID=your-project
#   ./deploy/cloudrun.sh setup      # once: APIs, managed database, secrets, service account
#   ./deploy/cloudrun.sh release    # every release: build, migrate, deploy
#   ./deploy/cloudrun.sh hosting    # optional: Firebase Hosting in front (custom domain, CDN)
#   ./deploy/cloudrun.sh seed-demo  # optional: simulated data for a demo deployment
#
# The database is always Cloud SQL, never a Postgres inside the container: data
# survives redeploys, is backed up daily with point-in-time recovery, and the
# instance is protected against accidental deletion.
#
# Region defaults to asia-south1 (Mumbai) so health data stays in India.
# Requires: gcloud (authenticated), run from the repository root.
# The hosting step also needs the Firebase CLI and Node.js.

set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID}"
REGION="${REGION:-asia-south1}"
SERVICE="${SERVICE:-swasthsetu}"
REPO="${REPO:-swasthsetu}"
SQL_INSTANCE="${SQL_INSTANCE:-swasthsetu-db}"
SQL_TIER="${SQL_TIER:-db-custom-1-3840}"
DB_NAME="${DB_NAME:-phc}"
DB_USER="${DB_USER:-swasthsetu_app}"
RUNTIME_SA="${SERVICE}-runtime@${PROJECT_ID}.iam.gserviceaccount.com"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${SERVICE}"
CONNECTION="${PROJECT_ID}:${REGION}:${SQL_INSTANCE}"
# Public demo deployments set this to true; real deployments leave it false.
PUBLIC_DEMO="${PUBLIC_DEMO:-false}"
# Connection budget: MAX_INSTANCES x (DB_POOL_SIZE + DB_MAX_OVERFLOW) must stay
# well under Cloud SQL's max_connections for the chosen tier, leaving room for
# migrations and administrative sessions. 6 x (4 + 2) = 36.
MAX_INSTANCES="${MAX_INSTANCES:-6}"
DB_POOL_SIZE="${DB_POOL_SIZE:-4}"
DB_MAX_OVERFLOW="${DB_MAX_OVERFLOW:-2}"

gc() { gcloud --project "$PROJECT_ID" "$@"; }
say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

secret_exists() { gc secrets describe "$1" >/dev/null 2>&1; }

create_secret() {
  local name="$1" value="$2"
  if secret_exists "$name"; then
    echo "secret $name already exists (unchanged)"
  else
    printf '%s' "$value" | gc secrets create "$name" --replication-policy=automatic --data-file=-
  fi
}

setup() {
  say "Enabling APIs"
  gc services enable run.googleapis.com sqladmin.googleapis.com \
    secretmanager.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
    routes.googleapis.com tile.googleapis.com

  say "Artifact Registry repository"
  gc artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1 ||
    gc artifacts repositories create "$REPO" --repository-format=docker --location "$REGION"

  say "Cloud SQL (managed PostgreSQL 17) — this takes several minutes the first time"
  if ! gc sql instances describe "$SQL_INSTANCE" >/dev/null 2>&1; then
    # --edition=ENTERPRISE: required for db-custom-* tiers (PostgreSQL 16+
    #   otherwise defaults to Enterprise Plus, which needs larger tiers).
    # Daily backups at 02:00 IST (20:30 UTC), kept 14 days, with
    #   point-in-time recovery; deletion protection on.
    # For a production launch, consider --availability-type=regional for a
    #   standby in a second zone (roughly doubles cost).
    gc sql instances create "$SQL_INSTANCE" \
      --database-version=POSTGRES_17 --edition=ENTERPRISE \
      --tier="$SQL_TIER" --region="$REGION" \
      --storage-type=SSD --storage-auto-increase \
      --backup-start-time=20:30 --retained-backups-count=14 \
      --enable-point-in-time-recovery \
      --deletion-protection \
      --availability-type=zonal
  fi
  gc sql databases describe "$DB_NAME" --instance "$SQL_INSTANCE" >/dev/null 2>&1 ||
    gc sql databases create "$DB_NAME" --instance "$SQL_INSTANCE"

  if ! secret_exists "${SERVICE}-database-url"; then
    # Hex keeps the password URL-safe inside the connection string.
    local db_password
    db_password="$(openssl rand -hex 24)"
    gc sql users create "$DB_USER" --instance "$SQL_INSTANCE" --password "$db_password" 2>/dev/null ||
      gc sql users set-password "$DB_USER" --instance "$SQL_INSTANCE" --password "$db_password"
    create_secret "${SERVICE}-database-url" \
      "postgresql+asyncpg://${DB_USER}:${db_password}@/${DB_NAME}?host=/cloudsql/${CONNECTION}"
  fi

  say "Session signing secret"
  create_secret "${SERVICE}-jwt-secret" "$(openssl rand -base64 48 | tr -d '\n')"

  say "Phone-number hashing salt"
  # Created once and never rotated casually: it is what keeps this database from
  # being turned back into a list of health workers' phone numbers, and changing
  # it orphans every handset already registered.
  create_secret "${SERVICE}-phone-salt" "$(openssl rand -base64 32 | tr -d '\n')"

  say "Runtime service account (least privilege)"
  gc iam service-accounts describe "$RUNTIME_SA" >/dev/null 2>&1 ||
    gc iam service-accounts create "${SERVICE}-runtime" --display-name "SwasthSetu runtime"
  for role in roles/cloudsql.client roles/secretmanager.secretAccessor; do
    gc projects add-iam-policy-binding "$PROJECT_ID" \
      --member "serviceAccount:${RUNTIME_SA}" --role "$role" --condition=None >/dev/null
  done

  say "Setup complete. Next: ./deploy/cloudrun.sh release"
}

common_flags=()
set_common_flags() {
  local secrets="DATABASE_URL=${SERVICE}-database-url:latest,JWT_SECRET=${SERVICE}-jwt-secret:latest,PHONE_HASH_SALT=${SERVICE}-phone-salt:latest"
  local env="ENVIRONMENT=production,DEMO_MODE=${PUBLIC_DEMO},ALLOW_PUBLIC_DEMO=${PUBLIC_DEMO},DB_POOL_SIZE=${DB_POOL_SIZE},DB_MAX_OVERFLOW=${DB_MAX_OVERFLOW}"
  # Google Maps turns on only when its server key has been stored as a secret:
  #   printf '%s' "$KEY" | gcloud secrets create swasthsetu-maps-server-key --data-file=-
  # The browser key is not secret (it is sent to every visitor); pass it as
  # MAPS_BROWSER_KEY when running this script.
  if secret_exists "${SERVICE}-maps-server-key"; then
    secrets="${secrets},GOOGLE_MAPS_SERVER_KEY=${SERVICE}-maps-server-key:latest"
    env="${env},MAPS_MODE=google,GOOGLE_MAPS_BROWSER_KEY=${MAPS_BROWSER_KEY:-}"
  fi
  common_flags=(
    --region "$REGION"
    --service-account "$RUNTIME_SA"
    --set-cloudsql-instances "$CONNECTION"
    --set-secrets "$secrets"
    --set-env-vars "$env"
  )
}

build() {
  local tag
  tag="$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)"
  say "Building ${IMAGE}:${tag} with Cloud Build"
  gc builds submit --tag "${IMAGE}:${tag}" .
  echo "${IMAGE}:${tag}"
}

run_job() {
  # One-off tasks (migrations, seeding) run as Cloud Run jobs on the same image,
  # with the same database connection and secrets as the service.
  local name="$1" image="$2"; shift 2
  set_common_flags
  if gc run jobs describe "$name" --region "$REGION" >/dev/null 2>&1; then
    gc run jobs update "$name" --image "$image" "${common_flags[@]}" --task-timeout=30m "$@"
  else
    gc run jobs create "$name" --image "$image" "${common_flags[@]}" --task-timeout=30m "$@"
  fi
  gc run jobs execute "$name" --region "$REGION" --wait
}

release() {
  local image
  image="$(build | tail -n1)"

  say "Migrating the database before the new version takes traffic"
  run_job "${SERVICE}-migrate" "$image" --command alembic --args upgrade,head

  say "Deploying ${SERVICE}"
  set_common_flags
  # Every request is short (live updates are polled), so the default request
  # timeout applies and any number of instances can serve any browser: shared
  # state (events, sign-in limits, sessions) lives in Cloud SQL.
  # --min-instances 1: no cold start for the first officer of the day.
  # --max-instances: caps cost and keeps within the database connection budget.
  # --allow-unauthenticated: the site handles its own sign-in; this only
  #   permits the request to reach the app (and Firebase Hosting's rewrite).
  gc run deploy "$SERVICE" --image "$image" "${common_flags[@]}" \
    --allow-unauthenticated \
    --port 8080 --cpu 1 --memory 1Gi --concurrency 80 \
    --timeout 60 --min-instances 1 --max-instances "$MAX_INSTANCES" \
    --cpu-boost --execution-environment gen2

  local url
  url="$(gc run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)')"
  say "Live at ${url}"
  curl -fsS "${url}/api/health" && echo
  cat <<EOF

Create the first administrator from your machine through the Cloud SQL Auth Proxy
(the password is prompted and never stored in cloud configuration):

  cloud-sql-proxy ${CONNECTION} --port 5433 &
  cd backend
  ENVIRONMENT=production JWT_SECRET=unused-for-this-command-xxxxxxxxxxxxxxxxxx DEMO_MODE=false \\
  DATABASE_URL="postgresql+asyncpg://${DB_USER}:<password from secret ${SERVICE}-database-url>@127.0.0.1:5433/${DB_NAME}" \\
    python -m scripts.users create --email you@department.gov.in --name "Your Name" --role admin

Custom domain and CDN: ./deploy/cloudrun.sh hosting, then add the domain in the Firebase console.
EOF
}

hosting() {
  command -v firebase >/dev/null || { echo "Install the Firebase CLI: npm install -g firebase-tools"; exit 1; }
  # firebase.json rewrites /api/** to a fixed service name and region; a
  # mismatch would send every API call to a service that does not exist.
  grep -q "\"serviceId\": \"${SERVICE}\"" firebase.json && grep -q "\"region\": \"${REGION}\"" firebase.json || {
    echo "firebase.json rewrites /api to a different service or region than SERVICE=${SERVICE} REGION=${REGION}. Update it first."
    exit 1
  }

  say "Linking Firebase to ${PROJECT_ID} (no-op if already linked)"
  firebase projects:addfirebase "$PROJECT_ID" >/dev/null 2>&1 || true

  say "Building the site"
  (cd frontend && npm ci --no-audit --no-fund && npm run build)

  say "Deploying Firebase Hosting (static site on the CDN, /api rewritten to Cloud Run)"
  firebase deploy --only hosting --project "$PROJECT_ID" --non-interactive
  echo "Add a custom domain under Hosting in the Firebase console; certificates are issued automatically."
}

seed_demo() {
  [[ "$PUBLIC_DEMO" == "true" ]] || { echo "Set PUBLIC_DEMO=true: seeding replaces all facility data."; exit 1; }
  local image
  image="$(gc run services describe "$SERVICE" --region "$REGION" --format 'value(spec.template.spec.containers[0].image)')"
  say "Seeding simulated national data (replaces facility data)"
  run_job "${SERVICE}-seed" "$image" --command python --args=-m,scripts.seed,--allow-production
}

case "${1:-}" in
  setup) setup ;;
  release) release ;;
  hosting) hosting ;;
  seed-demo) seed_demo ;;
  *) echo "usage: $0 {setup|release|hosting|seed-demo}"; exit 2 ;;
esac
