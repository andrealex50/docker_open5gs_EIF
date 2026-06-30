#!/usr/bin/env bash
set -euo pipefail

CORE_HOST=${CORE_HOST:-10.255.35.93}
CORE_DOCKER_SUBNET=${CORE_DOCKER_SUBNET:-172.22.0.0/24}

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this script as root on exigence2" >&2
  exit 1
fi

sysctl -w net.ipv4.ip_forward=1 >/dev/null
ip route replace "${CORE_DOCKER_SUBNET}" via "${CORE_HOST}"

echo "Route installed: ${CORE_DOCKER_SUBNET} via ${CORE_HOST}"
ip route get 172.22.0.7
