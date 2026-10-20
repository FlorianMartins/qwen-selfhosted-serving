#!/bin/bash
# Balaye les configs multi-slots de qwen-27b
M=/opt/llm/models/qwen3.8-27b/Qwen3.8-27B-UD-Q4_K_XL.gguf
COMMON="-m $M -ngl 99 -fa on --jinja --fit off --temp 1.0 --top-p 0.95 --top-k 20 --min-p 0.0"
run() { NAME=$1; NS=$2; shift 2
  echo "=== $NAME : $*"
  "$(dirname "$0")/try.sh" 10001 "${LOG_DIR:-/tmp}/t27b-$NAME.log" $COMMON "$@" || { echo "ÉCHEC"; return; }
  python3 -I "$(dirname "$0")"/bench_par.py 10001 $NS
  pgrep -x llama-server | xargs -r kill; sleep 5; }
run A "1"     -np 1 -c 65536
run B "1 2"   -np 2 -c 131072 -ctk q8_0 -ctv q8_0
run C "1 2 4" -np 4 -c 131072 -ctk q8_0 -ctv q8_0
run D "1 2 4" -np 4 -kvu -c 131072 -ctk q8_0 -ctv q8_0
run E "1 2"   -np 2 -c 131072 -ctk q8_0 -ctv q8_0 -b 2048 -ub 2048
