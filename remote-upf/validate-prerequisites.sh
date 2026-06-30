#!/usr/bin/env bash
set -euo pipefail

CORE_DOCKER_SUBNET=${CORE_DOCKER_SUBNET:-172.22.0.0/24}
FAILED=0

check() {
  local description=$1
  shift
  if "$@" >/dev/null 2>&1; then
    printf 'OK: %s\n' "${description}"
  else
    printf 'FAIL: %s\n' "${description}" >&2
    FAILED=1
  fi
}

check "bare-metal host" test "$(systemd-detect-virt 2>/dev/null || true)" = "none"
check "Docker available" command -v docker
check "Docker Compose available" docker compose version
check "TUN device available" test -c /dev/net/tun
check "RAPL available" test -d /sys/class/powercap/intel-rapl
check "route to exigence1 Docker network" ip route get "${CORE_DOCKER_SUBNET%/*}"

for port in 2152 8805; do
  if ss -lun | awk '{print $5}' | grep -Eq ":${port}$"; then
    echo "FAIL: UDP port ${port} is already in use" >&2
    FAILED=1
  else
    echo "OK: UDP port ${port} is available"
  fi
done

if ss -ltn | awk '{print $4}' | grep -Eq ':9091$'; then
  echo "FAIL: TCP port 9091 is already in use" >&2
  FAILED=1
else
  echo "OK: TCP port 9091 is available"
fi

exit "${FAILED}"
