#!/usr/bin/env bash
set -euo pipefail

REMOTE_UPF_HOST=${REMOTE_UPF_HOST:-10.255.35.34}
CORE_DOCKER_SUBNET=${CORE_DOCKER_SUBNET:-172.22.0.0/24}

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this script as root on exigence1" >&2
  exit 1
fi

sysctl -w net.ipv4.ip_forward=1 >/dev/null

if iptables -nL DOCKER-USER >/dev/null 2>&1; then
  iptables -C DOCKER-USER \
    -s "${REMOTE_UPF_HOST}" -d "${CORE_DOCKER_SUBNET}" \
    -p udp --dport 8805 -j ACCEPT 2>/dev/null || \
  iptables -I DOCKER-USER 1 \
    -s "${REMOTE_UPF_HOST}" -d "${CORE_DOCKER_SUBNET}" \
    -p udp --dport 8805 -j ACCEPT

  iptables -C DOCKER-USER \
    -s "${CORE_DOCKER_SUBNET}" -d "${REMOTE_UPF_HOST}" \
    -p udp --sport 8805 -j ACCEPT 2>/dev/null || \
  iptables -I DOCKER-USER 1 \
    -s "${CORE_DOCKER_SUBNET}" -d "${REMOTE_UPF_HOST}" \
    -p udp --sport 8805 -j ACCEPT
fi

echo "Core forwarding ready for PFCP between ${REMOTE_UPF_HOST} and ${CORE_DOCKER_SUBNET}"
