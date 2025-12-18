#!/usr/bin/env bash

set -euo pipefail

BASE="http://localhost:8080"

status() {
  local label="$1"; shift
  local url="$1"; shift
  curl -sS -m 8 -o /dev/null -w "${label} %{http_code} %{time_total}\n" "$url"
}

post_json() {
  local label="$1"; shift
  local url="$1"; shift
  local body="$1"; shift
  curl -sS -m 8 -o /dev/null -w "${label} %{http_code} %{time_total}\n" -X POST "$url" -H 'Content-Type: application/json' -d "$body"
}

echo "== HHMS Smoke Test via nginx (${BASE}) =="

echo "-- Health checks --"
status "gateway/health" "$BASE/health"
status "users/health" "$BASE/users/health"
status "patient/health" "$BASE/patient/health"
status "analytics/health" "$BASE/analytics/health"
status "alerts/health" "$BASE/alerts/health"

echo "-- Register patient --"
TS=$(date +%s)
EMAIL="smoke-${TS}@example.com"
PATIENT_JSON=$(curl -sS -X POST "$BASE/users/register" -H 'Content-Type: application/json' \
  -d "{\"email\":\"${EMAIL}\",\"name\":\"Smoke Test\",\"role\":\"patient\",\"phone\":\"+15555550155\"}")

PATIENT_ID=$(printf "%s" "$PATIENT_JSON" | python3 -c 'import sys, json; print(json.load(sys.stdin)["id"])')
if [[ -z "$PATIENT_ID" ]]; then
  echo "Failed to parse patient id; response: $PATIENT_JSON"
  exit 1
fi
echo "Registered patient id: $PATIENT_ID"

echo "-- Patient latest before ingestion (expect 404) --"
status "patient/latest" "$BASE/patient/$PATIENT_ID/latest"

echo "-- Ingest vitals --"
post_json "patient/ingest" "$BASE/patient/$PATIENT_ID" '{"heart_rate": 88, "spo2": 98}'

echo "-- Patient latest after ingestion (expect 200) --"
status "patient/latest" "$BASE/patient/$PATIENT_ID/latest"

echo "-- Analytics run trigger --"
post_json "analytics/run" "$BASE/analytics/run" '{}'

echo "-- Alerts send + history --"
post_json "alerts/send" "$BASE/alerts/send/$PATIENT_ID" '{"message":"Smoke test alert","severity":"INFO"}'
status "alerts/history" "$BASE/alerts/$PATIENT_ID"

echo "== Done =="
