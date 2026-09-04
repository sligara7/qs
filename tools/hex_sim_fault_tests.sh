#!/usr/bin/env bash
# Fault-injection drill for qs running the HEX profile against hex-ob/hex-simulated-beamline.
#
# Kills dependencies while plans run and checks that the HTTP API stays responsive, that
# failures are recorded and stop the queue (stop-and-wait), that /re/abort works while a
# plan is hung on dead hardware, and that plans succeed again once the dependency is back.
#
#   QS_URL=http://127.0.0.1:60610 QS_API_KEY=hex tools/hex_sim_fault_tests.sh [A|B|C|D ...]
#
# Needs: a running qs on the HEX profile, docker (containers hexsim-tiled, hexsim-redis,
# hexsim-kinetix-ioc), and the sim's Tiled catalog seeded. Tiled runs `--temp`, so the
# catalog is lost when its container restarts: test B re-seeds with scripts/seed.sh from
# HEX_SIM_DIR (default ~/git_projects/hex-ob/hex-simulated-beamline).
# Do not run against a real beamline.
set -u
B="${QS_URL:-http://127.0.0.1:60610}/api"; H="Authorization: ApiKey ${QS_API_KEY:-hex}"; J="Content-Type: application/json"
SIM="${HEX_SIM_DIR:-$HOME/git_projects/hex-ob/hex-simulated-beamline}"
TESTS="${*:-A B C D}"

st() { curl -s -m 5 -H "$H" "$B/status" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['items_in_history'], d['manager_state'], d['re_state'], d['running_item_uid'] or '-')" 2>/dev/null || echo "API-UNRESPONSIVE"; }
lat() { curl -s -m 5 -o /dev/null -w "%{time_total}" -H "$H" "$B/status"; }
add() { curl -s -X POST -H "$H" -H "$J" -d "{\"item\":{\"item_type\":\"plan\",\"name\":\"$1\",\"args\":$2,\"kwargs\":$3}}" "$B/queue/item/add" | python3 -c "import json,sys; d=json.load(sys.stdin); print('  add', d['success'], d.get('msg',''))"; }
post() { curl -s -X POST -H "$H" -H "$J" -d '{}' "$B/$1" | python3 -c "import json,sys; d=json.load(sys.stdin); print('  $1 ->', d['success'], d.get('msg',''))"; }
wait_idle() { local t0=$(date +%s) n el; while :; do n=$(st); el=$(( $(date +%s)-t0 )); case "$n" in *" idle idle -") echo "  idle after ${el}s"; return 0;; esac; [ "$el" -ge "$1" ] && { echo "  STILL BUSY after ${el}s: $n"; return 1; }; sleep 2; done; }
wait_running() { local t0=$(date +%s) n; while :; do n=$(st); case "$n" in *running*) return 0;; esac; [ $(( $(date +%s)-t0 )) -ge "$1" ] && { echo "  never started running: $n"; return 1; }; sleep 0.5; done; }
last() { curl -s -H "$H" "$B/history/get" | python3 -c "
import json,sys; it=json.load(sys.stdin)['items'][-1]; r=it['result']
print(f\"  RESULT {it['name']} {it['args']} {it['kwargs']} -> {r['exit_status']} runs={len(r['run_uids'])} {r['time_stop']-r['time_start']:.1f}s msg={r['msg'][:200]!r}\")"; }
recover() { echo "  -- recovery run"; add count '[["kinetix1"]]' '{"num":2}'; post queue/start; wait_idle 60 || { post re/abort; wait_idle 30; }; last; }

for t in $TESTS; do case "$t" in
A) echo "### A. abort a long software plan (sleep_for_secs 120)"
   add sleep_for_secs '[120]' '{}'; post queue/start; wait_running 10; sleep 2; echo "  while running: $(st) latency=$(lat)s"
   post re/abort; wait_idle 30; last ;;
B) echo "### B. Tiled down during count([kinetix1], num=2)"
   docker stop hexsim-tiled >/dev/null && echo "  tiled stopped"
   add count '[["kinetix1"]]' '{"num":2}'; post queue/start
   wait_idle 60 || { echo "  hung with tiled down -> abort"; post re/abort; wait_idle 30; }; last
   docker start hexsim-tiled >/dev/null; for i in $(seq 1 30); do curl -s -m 2 -o /dev/null http://127.0.0.1:8000/api/v1/ && break; sleep 1; done
   echo "  tiled back; re-seeding (--temp catalog)"; bash "$SIM/scripts/seed.sh" >/dev/null 2>&1; recover ;;
C) echo "### C. Kinetix IOC down mid-acquisition, abort 6s later (count num=60, delay=0.5)"
   add count '[["kinetix1"]]' '{"num":60,"delay":0.5}'; post queue/start; wait_running 10; sleep 3
   docker stop hexsim-kinetix-ioc >/dev/null && echo "  kinetix-ioc stopped"; sleep 6; echo "  t+6s: $(st) latency=$(lat)s"
   t0=$(date +%s); post re/abort; wait_idle 60; echo "  abort->idle took $(( $(date +%s)-t0 ))s"; last
   docker start hexsim-kinetix-ioc >/dev/null; echo "  kinetix-ioc restarted; waiting 25s"; sleep 25; recover ;;
D) echo "### D. Redis down (RE.md is a RedisJSONDict) during count([kinetix1], num=2)"
   docker stop hexsim-redis >/dev/null && echo "  redis stopped"
   add count '[["kinetix1"]]' '{"num":2}'; post queue/start
   wait_idle 60 || { echo "  hung with redis down -> abort"; post re/abort; wait_idle 30; }; last
   docker start hexsim-redis >/dev/null; sleep 5; echo "  redis back"; recover ;;
*) echo "unknown test $t" ;;
esac; echo; done
echo "### final: $(st)"
