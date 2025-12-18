#!/usr/bin/env bash

set -euo pipefail

BASE="http://localhost:8080"

PHONE=${1:-"+15555550123"}
MESSAGE=${2:-"Hello from HHMS real SMS test"}

echo "== HHMS Real SMS & Anomaly Test =="
echo "Using phone: ${PHONE}"

# Validate E.164 format (basic check)
if [[ ! "$PHONE" =~ ^\+?[0-9]{10,15}$ ]]; then
  echo "Error: phone must be in E.164 format (e.g., +15555550123)" >&2
  exit 1
fi
if [[ "$PHONE" != +* ]]; then
  echo "Warning: phone missing '+' prefix; Twilio requires E.164. Try: +${PHONE}" >&2
fi

health() {
  local name="$1"; shift
  local url="$1"; shift
  curl -sS -m 8 -o /dev/null -w "${name} %{http_code} %{time_total}\n" "$url"
}

post_json() {
  local name="$1"; shift
  local url="$1"; shift
  local body="$1"; shift
  curl -sS -m 8 -o /dev/null -w "${name} %{http_code} %{time_total}\n" -X POST "$url" -H 'Content-Type: application/json' -d "$body"
}

echo "-- Health checks --"
health "gateway/health" "$BASE/health"
health "users/health" "$BASE/users/health"
health "patient/health" "$BASE/patient/health"
health "analytics/health" "$BASE/analytics/health"
health "alerts/health" "$BASE/alerts/health"

echo "-- Twilio system status --"
curl -sS -m 8 "$BASE/alerts/system/status" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("twilio_api:", d.get("twilio"))'

echo "-- Register patient with provided phone --"
TS=$(date +%s)
EMAIL="real-sms-${TS}@example.com"
PATIENT_JSON=$(curl -sS -X POST "$BASE/users/register" -H 'Content-Type: application/json' \
  -d "{\"email\":\"${EMAIL}\",\"name\":\"Real SMS Test\",\"role\":\"patient\",\"phone\":\"${PHONE}\"}")
PATIENT_ID=$(printf "%s" "$PATIENT_JSON" | python3 -c 'import sys, json; print(json.load(sys.stdin)["id"])')
echo "Patient ID: $PATIENT_ID"

echo "-- Send direct SMS via alerts service --"
ALERT_JSON=$(curl -sS -X POST "$BASE/alerts/notifications/send" -H 'Content-Type: application/json' \
  -d "{\"patient_id\":\"$PATIENT_ID\",\"message\":\"${MESSAGE}\",\"severity\":\"INFO\"}")
echo "$ALERT_JSON" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("alert_status:", d.get("status"), "recipient:", d.get("recipient_phone"), "error:", d.get("error_message"))'
echo "Check your phone for the SMS (from TWILIO_FROM_NUMBER)."

echo "-- Configure threshold to force anomaly (heart_rate max=60) --"
curl -sS -X POST "$BASE/analytics/thresholds" -H 'Content-Type: application/json' \
  -d "{\"patient_id\":\"$PATIENT_ID\",\"metric\":\"heart_rate\",\"min_value\":null,\"max_value\":60}" >/dev/null

echo "-- Ingest violating vitals and wait for processing --"
post_json "patient/ingest" "$BASE/patient/$PATIENT_ID" '{"heart_rate": 95, "spo2": 97}'
sleep 2

echo "-- Confirm anomalies and alerts --"
curl -sS "$BASE/analytics/anomalies/$PATIENT_ID" | python3 -c 'import sys,json; a=json.load(sys.stdin); print("anomalies_count:", len(a), "latest:", a[0]["description"] if a else None)'
curl -sS "$BASE/alerts/$PATIENT_ID" | python3 -c 'import sys,json; arr=json.load(sys.stdin); print("alerts_count:", len(arr), "latest_status:", arr[0]["status"] if arr else None, "latest_message:", arr[0]["message"] if arr else None)'

echo "== Completed real SMS & anomaly test =="
