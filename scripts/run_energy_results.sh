#!/usr/bin/env bash
set -euo pipefail

MODE=${1:-smoke}
PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
COLLECTOR_URL=${COLLECTOR_URL:-http://172.22.0.44:8088}

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo: sudo ./scripts/run_energy_results.sh ${MODE}" >&2
  exit 1
fi

case "${MODE}" in
  prepare|smoke|full) ;;
  *)
    echo "Usage: sudo ./scripts/run_energy_results.sh {prepare|smoke|full}" >&2
    exit 1
    ;;
esac

cd "${PROJECT_ROOT}"

echo "Recreating the Energy Collector with the current code and environment"
docker compose -f sa-deploy.yaml up -d --build --force-recreate energy-collector

echo "Waiting for ${COLLECTOR_URL}/health"
for _ in $(seq 1 30); do
  if curl -fsS "${COLLECTOR_URL}/health" >/dev/null; then
    break
  fi
  sleep 1
done
curl -fsS "${COLLECTOR_URL}/health" | jq .
curl -fsS "${COLLECTOR_URL}/energy-sources/status" | jq .

if [ "${MODE}" = "prepare" ]; then
  exit 0
fi

if [ "${MODE}" = "smoke" ]; then
  exec python3 scripts/run_energy_experiments.py \
    --config experiments/energy-baseline-local-upf.json \
    --scenario idle \
    --scenario udp-ul-10m \
    --repetitions 1 \
    --label smoke-local-upf
fi

exec python3 scripts/run_energy_experiments.py \
  --config experiments/energy-baseline-local-upf.json \
  --continue-on-error
