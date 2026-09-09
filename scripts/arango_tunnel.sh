#!/usr/bin/env bash
# Bring up (or repair) the two hops to ArangoDB. Safe to re-run: it checks first.
#
# The database is a pod in a k8s cluster on another host, reached through two
# forwarding hops that both die with a reboot -- and the remote one has been
# observed dying on its own mid-session. `docker ps` shows nothing, which makes
# it look like the container was lost. It was not. See D-98.
set -euo pipefail
HOST="${LAGMATRIX_ARANGO_HOST:-ridopark@192.168.10.123}"
PORT="${LAGMATRIX_ARANGO_PORT:-19999}"

# A listening socket is NOT proof the path works: a stale local tunnel keeps
# accepting connections after the remote port-forward dies, which is exactly how
# this failed the second time -- `serve.arango_db()`'s socket pre-check said
# "reachable" while every query aborted.  Probe the HTTP endpoint instead.
if curl -s -m 4 -o /dev/null "http://127.0.0.1:$PORT/_api/version"; then
  echo "tunnel already up on $PORT"; exit 0
fi

echo "restarting remote port-forward…"
ssh -o BatchMode=yes -o ConnectTimeout=8 "$HOST" \
  "pkill -f 'port-forward.*arangodb' >/dev/null 2>&1 || true
   setsid nohup kubectl -n lagmatrix port-forward --address 127.0.0.1 deploy/arangodb $PORT:8529 \
     </dev/null >/tmp/arango-pf.log 2>&1 & disown
   sleep 5
   curl -s -o /dev/null -w 'remote probe: %{http_code}\n' http://127.0.0.1:$PORT/_api/version"

echo "restarting local tunnel…"
pkill -f "ssh .*-L $PORT:127.0.0.1:$PORT" >/dev/null 2>&1 || true
ssh -f -N -o BatchMode=yes -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
    -L "$PORT:127.0.0.1:$PORT" "$HOST"
sleep 2
curl -s -o /dev/null -w "local probe: %{http_code} (401 = up, needs auth)\n" \
  "http://127.0.0.1:$PORT/_api/version"
