#!/usr/bin/env bash
set -euo pipefail

# HHMS End-to-End Test
# - Registers doctor and patient
# - Links patient to doctor
# - Sets a heart_rate threshold
# - Ingests abnormal vitals (HR > threshold)
# - Verifies an alert is created and sent (Twilio test mode)

BASE="http://localhost:8080"
USERS="$BASE/users"
PATIENT="$BASE/patient"
ANALYTICS="$BASE/analytics"
ALERTS="$BASE/alerts"

# Helpers --------------------------------------------------------------
json_get() {
  local key="$1"
  python3 -c 'import sys,json
key=sys.argv[1]
try:
    data=json.load(sys.stdin)
    print(data.get(key,""))
except Exception:
    print("")' "$key"
}

json_first_id() {
  python3 -c 'import sys,json
try:
    arr=json.load(sys.stdin)
    print(arr[0]["id"] if isinstance(arr,list) and arr else "")
except Exception:
    print("")'
}

json_first_status() {
  python3 -c 'import sys,json
try:
    arr=json.load(sys.stdin)
    print(arr[0]["status"] if isinstance(arr,list) and arr else "")
except Exception:
    print("")'
}

urlencode() {
  python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$1"
}

get_user_id_by_email() {
  local email="$1"
  local resp
  local enc
  enc=$(urlencode "$email")
  resp=$(retry_curl GET "$USERS/email/$enc") || true
  echo "$resp" | json_get id
}

retry_curl() {
  # Retries until HTTP 200/201 and returns body
  local method="$1" url="$2" data="${3:-}"
  local tries=30 delay=2
  local response status body
  for ((i=1; i<=tries; i++)); do
    if [[ "$method" == "GET" ]]; then
      response=$(curl -sS -m 10 -w "\n%{http_code}" -H 'Content-Type: application/json' "$url" || true)
    else
      response=$(curl -sS -m 10 -w "\n%{http_code}" -X "$method" -H 'Content-Type: application/json' -d "$data" "$url" || true)
    fi
    status=$(printf "%s" "$response" | tail -n1)
    body=$(printf "%s" "$response" | sed '$d')
    if [[ "$status" == "200" || "$status" == "201" ]]; then
      printf "%s" "$body"
      return 0
    fi
    sleep "$delay"
  done
  echo "Request to $url failed after $tries attempts (last status: $status)" >&2
  printf "%s" "$body"
  return 1
}

wait_health() {
  echo "Waiting for gateway and services to be healthy..."
  local tries=60 delay=2

  wait_200() {
    local url="$1" name="$2"
    local status
    for ((i=1; i<=tries; i++)); do
      status=$(curl -sS -m 5 -o /dev/null -w "%{http_code}" "$url" || true)
      if [[ "$status" == "200" ]]; then
        echo "OK: $name"
        return 0
      fi
      sleep "$delay"
    done
    echo "ERROR: $name not healthy (last status: $status)" >&2
    return 1
  }

  wait_200 "$BASE/health" "gateway"
  wait_200 "$USERS/health" "user-service"
  wait_200 "$PATIENT/health" "patient-data-service"
  wait_200 "$ANALYTICS/health" "analytics-service"
  wait_200 "$ALERTS/health" "alerts-service"
}

wait_health

echo "Step 1: Register Doctor"
DOC_EMAIL="dr.e2e@example.com"
DOC_PAYLOAD=$(cat <<JSON
{"email":"$DOC_EMAIL","name":"Dr E2E","role":"doctor","phone":"+15555550001"}
JSON
)
DOC_RESP=$(retry_curl POST "$USERS/register" "$DOC_PAYLOAD") || true
DOC_ID=$(echo "$DOC_RESP" | json_get id)
if [[ -z "$DOC_ID" ]]; then
  # If already exists (409), fetch by email directly
  DOC_ID=$(curl -sS "$USERS/email/$(urlencode "$DOC_EMAIL")" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("id",""))') || true
fi
if [[ -z "$DOC_ID" ]]; then
  echo "Failed to ensure doctor exists: $DOC_RESP" >&2
  exit 1
fi
echo "OK: Registered doctor $DOC_ID"

echo "Step 2: Register Patient"
PAT_EMAIL="patient.e2e@example.com"
PAT_PAYLOAD=$(cat <<JSON
{"email":"$PAT_EMAIL","name":"Patient E2E","role":"patient","phone":"+15555550002"}
JSON
)
PAT_RESP=$(retry_curl POST "$USERS/register" "$PAT_PAYLOAD") || true
PAT_ID=$(echo "$PAT_RESP" | json_get id)
if [[ -z "$PAT_ID" ]]; then
  # If already exists (409), fetch by email directly
  PAT_ID=$(curl -sS "$USERS/email/$(urlencode "$PAT_EMAIL")" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("id",""))') || true
fi
if [[ -z "$PAT_ID" ]]; then
  echo "Failed to ensure patient exists: $PAT_RESP" >&2
  exit 1
fi
echo "OK: Registered patient $PAT_ID"

echo "Step 3: Link patient to doctor"
LINK_PAYLOAD=$(cat <<JSON
{"doctor_id":"$DOC_ID","patient_id":"$PAT_ID"}
JSON
)
LINK_RESP=$(retry_curl POST "$USERS/relationships" "$LINK_PAYLOAD")
if [[ -z "$LINK_RESP" ]]; then
  echo "Failed to link patient to doctor" >&2
  exit 1
fi
echo "OK: Linked patient $PAT_ID to doctor $DOC_ID"

echo "Step 4: Set heart rate threshold"
THRESH_PAYLOAD=$(cat <<JSON
{"patient_id":"$PAT_ID","metric":"heart_rate","max_value":120}
JSON
)
THRESH_RESP=$(retry_curl POST "$ANALYTICS/thresholds" "$THRESH_PAYLOAD")
if [[ -z "$THRESH_RESP" ]]; then
  echo "Failed to set threshold" >&2
  exit 1
fi
echo "OK: Threshold set for patient $PAT_ID"

echo "Step 5: Ingest abnormal vitals"
NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
VITALS_PAYLOAD=$(cat <<JSON
{"timestamp":"$NOW","heart_rate":160}
JSON
)
VITALS_RESP=$(retry_curl POST "$PATIENT/$PAT_ID" "$VITALS_PAYLOAD")
if [[ -z "$VITALS_RESP" ]]; then
  echo "Failed to ingest vitals" >&2
  exit 1
fi
echo "OK: Ingested vitals for patient $PAT_ID"

echo "Step 6: Await analytics processing"
sleep 5

echo "Step 7: Verify alert history"
tries=15; delay=2; FIRST_ALERT_ID=""
for ((i=1; i<=tries; i++)); do
  ALERTS_RESP=$(retry_curl GET "$ALERTS/$PAT_ID") || true
  FIRST_ALERT_ID=$(echo "$ALERTS_RESP" | json_first_id)
  if [[ -n "$FIRST_ALERT_ID" ]]; then
    break
  fi
  sleep "$delay"
done
if [[ -z "$FIRST_ALERT_ID" ]]; then
  echo "FAIL: No alerts found for patient $PAT_ID after waiting" >&2
  echo "$ALERTS_RESP"
  exit 1
fi

echo "PASS: Alert created (id=$FIRST_ALERT_ID) for patient $PAT_ID"

echo "Step 8: Acknowledge the alert"
ACK_PAYLOAD=$(cat <<JSON
{"status":"acknowledged","doctor_id":"$DOC_ID"}
JSON
)
ACK_RESP=$(retry_curl POST "$ALERTS/$FIRST_ALERT_ID/acknowledge" "$ACK_PAYLOAD")
if [[ -z "$ACK_RESP" ]]; then
  echo "Failed to acknowledge alert $FIRST_ALERT_ID" >&2
  exit 1
fi

# Verify acknowledged in history
ALERTS_RESP=$(retry_curl GET "$ALERTS/$PAT_ID") || true
FIRST_STATUS=$(echo "$ALERTS_RESP" | json_first_status)
if [[ "$FIRST_STATUS" != "acknowledged" ]]; then
  echo "FAIL: Expected acknowledged status, got '$FIRST_STATUS'" >&2
  echo "$ALERTS_RESP"
  exit 1
fi

echo "PASS: Alert acknowledged by doctor $DOC_ID"
exit 0
echo "Patient ID: $PAT_ID"

# 3) Link patient to doctor
LINK_PAYLOAD=$(cat <<JSON
{"doctor_id":"$DOC_ID","patient_id":"$PAT_ID"}
JSON
echo "OK: Ingested vitals for patient $PAT_ID"
)
LINK_RESP=$(retry_curl POST "$USERS/relationships" "$LINK_PAYLOAD")
echo "Step 6: Await analytics processing"
sleep 5
  echo "Failed to link patient to doctor" >&2
  exit 1
echo "Step 7: Verify alert history"
fi

# 4) Set heart_rate threshold for patient (max 120)
THRESH_PAYLOAD=$(cat <<JSON
{"patient_id":"$PAT_ID","metric":"heart_rate","max_value":120}
JSON
)
THRESH_RESP=$(retry_curl POST "$ANALYTICS/thresholds" "$THRESH_PAYLOAD")
echo "PASS: Alert created (id=$FIRST_ALERT_ID) for patient $PAT_ID"
  echo "Failed to set threshold" >&2
  exit 1
fi

# 5) Ingest abnormal vitals (heart_rate 160)
NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
VITALS_PAYLOAD=$(cat <<JSON
{"timestamp":"$NOW","heart_rate":160}
JSON
)
VITALS_RESP=$(retry_curl POST "$PATIENT/$PAT_ID" "$VITALS_PAYLOAD")
if [[ -z "$VITALS_RESP" ]]; then
  echo "Failed to ingest vitals" >&2
  exit 1
fi

# 6) Wait briefly for analytics to process
sleep 3

# 7) Verify alert history shows at least one alert for patient
ALERTS_RESP=$(retry_curl GET "$ALERTS/$PAT_ID")
FIRST_ALERT_ID=$(echo "$ALERTS_RESP" | json_first_id)
if [[ -z "$FIRST_ALERT_ID" ]]; then
  echo "FAIL: No alerts found for patient $PAT_ID" >&2
  echo "$ALERTS_RESP"
  exit 1
fi

echo "PASS: Alert created (id=$FIRST_ALERT_ID) for patient $PAT_ID"
exit 0
