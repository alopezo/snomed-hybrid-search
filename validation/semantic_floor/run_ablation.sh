#!/bin/bash
# Semantic-floor ablation: does dropping weak semantic-only candidates (SEM_MIN_SIM) cost recall?
# Runs the search-only eval twice against throwaway API instances that differ only in SEM_MIN_SIM:
# 0 (floor off) vs the served value (floor on, applied only to queries that look truncated).
# Pre-process OFF on purpose: that is where the semantic channel works alone on untranslated Spanish,
# i.e. where a floor could hurt. (With pre-process ON the embedded text is the gemma expansion — whole
# words — so the gate never fires.) Needs the DB up; the served API on :8090 is not touched.
#
#   bash validation/semantic_floor/run_ablation.sh distemist 750
set -e
cd "$(dirname "$0")/../.."
CORPUS="${1:-distemist}"
N="${2:-750}"
PY=.venv/bin/python

run() {   # $1 = SEM_MIN_SIM, $2 = port, $3 = tag
  SEM_MIN_SIM=$1 $PY -m uvicorn api.server:app --host 127.0.0.1 --port $2 >/tmp/semfloor-$2.log 2>&1 &
  local pid=$!
  until curl -sf http://127.0.0.1:$2/api/health >/dev/null; do sleep 2; done
  echo "===== [SEM_MIN_SIM=$1] ====="
  VALIDATION_API=http://127.0.0.1:$2 $PY -u validation/run_search_eval.py \
    --corpus $CORPUS --n $N --gemma false --tag $3
  kill $pid
}
run 0    8091 _floor000
run 0.80 8093 _floor080trunc
echo "===== ABLATION DONE ($CORPUS) ====="
