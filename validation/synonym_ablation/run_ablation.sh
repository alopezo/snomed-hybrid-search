#!/bin/bash
# Synonym-value ablation: how much do SNOMED CT synonyms contribute to retrieval, per channel?
# Sweeps channel x desc_scope over one corpus. desc_scope: all (FSN+synonyms) / fsn_pt (FSN+preferred
# term) / fsn (FSN only). Per-channel runs use rerank OFF (raw retrieval isolation); the served config
# is both + rerank ON. gemma OFF and LOINC excluded throughout, tag _synab so files stay grouped.
#
#   bash validation/synonym_ablation/run_ablation.sh distemist 100000
#   bash validation/synonym_ablation/run_ablation.sh snomed_el 25
set -e
cd "$(dirname "$0")/../.."
CORPUS="${1:-distemist}"
N="${2:-100000}"
PY=.venv/bin/python
if [ "$CORPUS" = "distemist" ]; then SCOPE="disorder,finding"; SBT=""; else SCOPE="all"; SBT="--scope-by-type"; fi
COMMON="--corpus $CORPUS --n $N --gemma false $SBT --scope $SCOPE --exclude-module 11010000107 --tag _synab"

for DS in all fsn_pt fsn; do
  echo "===== [semantic|rrOFF|$DS] ====="; $PY -u validation/run_search_eval.py $COMMON --channel semantic --rerank false --desc-scope $DS
  echo "===== [lexical|rrOFF|$DS]  ====="; $PY -u validation/run_search_eval.py $COMMON --channel lexical  --rerank false --desc-scope $DS
done
for DS in all fsn_pt fsn; do
  echo "===== [both|rrON|$DS] ====="; $PY -u validation/run_search_eval.py $COMMON --channel both --rerank true --desc-scope $DS
done
echo "===== ABLATION DONE ($CORPUS) ====="
