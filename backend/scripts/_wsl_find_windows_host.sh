#!/usr/bin/env bash
set -euo pipefail
WINIP=$(grep -m1 nameserver /etc/resolv.conf | awk '{print $2}')
GW=$(ip route | awk '/default/ {print $3; exit}')
echo "WINIP=${WINIP}"
echo "GW=${GW}"
for host in "$WINIP" "$GW" "172.17.64.1"; do
  code=$(curl -sS -m 4 -o /dev/null -w '%{http_code}' "http://${host}:9000/" || true)
  echo "probe ${host}:9000 -> ${code}"
done
