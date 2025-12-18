#!/usr/bin/env bash

set -euo pipefail

BASE="http://localhost:8080"

echo "== HHMS Twilio (test-mode) & Anomaly Detection Test =="

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

echo "-- Register patient --"
TS=$(date +%s)
EMAIL="twilio-anom-${TS}@example.com"
PATIENT_JSON=$(curl -sS -X POST "$BASE/users/register" -H 'Content-Type: application/json' \
  -d "{\"email\":\"${EMAIL}\",\"name\":\"Twilio Anomaly\",\"role\":\"patient\",\"phone\":\"+15555550199\"}")
PATIENT_ID=$(printf "%s" "$PATIENT_JSON" | python3 -c 'import sys, json; print(json.load(sys.stdin)["id"])')
echo "Patient ID: $PATIENT_ID"

if [[ -z "$PATIENT_ID" ]]; then
  echo "Failed to parse patient id; response: $PATIENT_JSON" >&2
  exit 1
fi

echo "-- Direct Twilio notification (test mode) --"
ALERT_JSON=$(curl -sS -X POST "$BASE/alerts/notifications/send" -H 'Content-Type: application/json' \
  -d "{\"patient_id\":\"$PATIENT_ID\",\"message\":\"Hello from test script\",\"severity\":\"INFO\"}")
STATUS=$(printf "%s" "$ALERT_JSON" | python3 -c 'import sys, json; print(json.load(sys.stdin)["status"])')
PROVIDER_ID=$(printf "%s" "$ALERT_JSON" | python3 -c 'import sys, json; print(json.load(sys.stdin).get("provider_message_id",""))')
echo "Alert status: $STATUS provider_id: $PROVIDER_ID"

if [[ "$STATUS" != "sent" ]]; then
  echo "Warning: alert status is not 'sent'. Check TWILIO_TEST_MODE or credentials." >&2
fi

echo "-- Configure threshold to force anomaly (heart_rate max=60) --"
THRESH_JSON=$(curl -sS -X POST "$BASE/analytics/thresholds" -H 'Content-Type: application/json' \
  -d "{\"patient_id\":\"$PATIENT_ID\",\"metric\":\"heart_rate\",\"min_value\":null,\"max_value\":60}")
echo "Threshold: $(printf "%s" "$THRESH_JSON" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("id=%s metric=%s max=%s" % (d.get("id"), d.get("metric"), d.get("max_value")))')"

echo "-- Ingest violating vitals (heart_rate=95) --"
post_json "patient/ingest" "$BASE/patient/$PATIENT_ID" '{"heart_rate": 95, "spo2": 97}'

echo "Waiting for analytics listener to process..."
sleep 2

echo "-- Check anomalies --"
ANOMS=$(curl -sS "$BASE/analytics/anomalies/$PATIENT_ID")
COUNT=$(printf "%s" "$ANOMS" | python3 -c 'import sys, json; print(len(json.load(sys.stdin)))')
echo "Anomalies count: $COUNT"
if [[ "$COUNT" -lt 1 ]]; then
  echo "Error: expected >=1 anomalies after violating threshold." >&2
  exit 2
fi

DESC=$(printf "%s" "$ANOMS" | python3 -c 'import sys,json; a=json.load(sys.stdin); print(a[0]["description"])')
echo "Latest anomaly: $DESC"

echo "-- Check alerts history (should include anomaly-triggered alert) --"
ALERTS=$(curl -sS "$BASE/alerts/$PATIENT_ID")
ACOUNT=$(printf "%s" "$ALERTS" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)))')
echo "Alerts count: $ACOUNT"

LAST_STATUS=$(printf "%s" "$ALERTS" | python3 -c 'import sys,json; arr=json.load(sys.stdin); print(arr[0]["status"])')
LAST_MSG=$(printf "%s" "$ALERTS" | python3 -c 'import sys,json; arr=json.load(sys.stdin); print(arr[0]["message"])')
echo "Latest alert status: $LAST_STATUS message: $LAST_MSG"

echo "== Completed Twilio & Anomaly test =="
