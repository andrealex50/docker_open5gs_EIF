#!/usr/bin/env bash
set -euo pipefail

COLLECTOR_URL=${COLLECTOR_URL:-http://172.22.0.44:8088}
EIF_URL=${EIF_URL:-http://172.22.0.43:7777}
NOTIF_URI=${NOTIF_URI:-http://172.22.0.45:9998/notify}
SUPI=${SUPI:-imsi-001011234567895}
UE_IP=${UE_IP:-192.168.100.2}
EVENT=${EVENT:-UE_ENERGY}
TX_BYTES=${TX_BYTES:-1000000}
RX_BYTES=${RX_BYTES:-5000000}
SOURCE=${SOURCE:-manual}
APP_ID=${APP_ID:-validation-flow}
FLOW_DESC=${FLOW_DESC:-permit out ip from any to assigned}
START=${START:-$(date -u -d '2 minutes ago' +%Y-%m-%dT%H:%M:%SZ)}
END=${END:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}
SAMPLE_TIMESTAMP=${SAMPLE_TIMESTAMP:-${END}}
CREATE_EIF_SUBSCRIPTION=${CREATE_EIF_SUBSCRIPTION:-false}

pretty_json() {
  if command -v jq >/dev/null 2>&1; then
    jq .
  else
    cat
  fi
}

section() {
  printf '\n# %s\n' "$1"
}

section "Collector health"
curl -sS "${COLLECTOR_URL}/health" | pretty_json

section "Energy source status"
curl -sS "${COLLECTOR_URL}/energy-sources/status" | pretty_json

section "Energy source window"
curl -sS "${COLLECTOR_URL}/energy-sources/window?start=${START}&end=${END}" | pretty_json

section "Register UE mapping"
curl -sS -X POST "${COLLECTOR_URL}/ue-mappings" \
  -H "Content-Type: application/json" \
  -d "{
    \"supi\": \"${SUPI}\",
    \"ue_ip\": \"${UE_IP}\",
    \"source\": \"manual\"
  }" | pretty_json

section "Insert validation traffic sample"
curl -sS -X POST "${COLLECTOR_URL}/samples/traffic" \
  -H "Content-Type: application/json" \
  -d "{
    \"supi\": \"${SUPI}\",
    \"ue_ip\": \"${UE_IP}\",
    \"timestamp\": \"${SAMPLE_TIMESTAMP}\",
    \"tx_bytes\": ${TX_BYTES},
    \"rx_bytes\": ${RX_BYTES},
    \"source\": \"${SOURCE}\",
    \"appId\": \"${APP_ID}\",
    \"flowDescs\": [\"${FLOW_DESC}\"]
  }" | pretty_json

section "Collector energy report"
curl -sS "${COLLECTOR_URL}/energy/v1/report?supi=${SUPI}&event=${EVENT}&start=${START}&end=${END}" | pretty_json

section "Service-flow scoped energy report"
curl -G -sS "${COLLECTOR_URL}/energy/v1/report" \
  --data-urlencode "supi=${SUPI}" \
  --data-urlencode "event=SERVICE_FLOW_ENERGY" \
  --data-urlencode "start=${START}" \
  --data-urlencode "end=${END}" \
  --data-urlencode "appId=${APP_ID}" \
  --data-urlencode "flowDescs=${FLOW_DESC}" | pretty_json

section "Latest stored attributions"
curl -sS "${COLLECTOR_URL}/energy-sources/attributions?limit=5" | pretty_json

if docker ps --format '{{.Names}}' | grep -qx mongo; then
  section "MongoDB energy source collections"
  docker exec mongo mongosh energy_collector --quiet --eval '
print("energy_source_samples=" + db.energy_source_samples.countDocuments());
print("energy_attributions=" + db.energy_attributions.countDocuments());
db.energy_attributions.find({}, {_id:0}).sort({timestamp:-1}).limit(2).pretty();
'
fi

if [ "${CREATE_EIF_SUBSCRIPTION}" = "true" ]; then
  section "Create EIF subscription"
  curl --http2-prior-knowledge -sS -i \
    "${EIF_URL}/neif-ee/v1/subscriptions" \
    -H "Content-Type: application/json" \
    -d "{
      \"notifUri\": \"${NOTIF_URI}\",
      \"eventsSubscSets\": {
        \"validation1\": {
          \"subscSetId\": \"validation1\",
          \"event\": \"${EVENT}\",
          \"supi\": \"${SUPI}\",
          \"repPeriod\": 5
        }
      }
    }"
fi
