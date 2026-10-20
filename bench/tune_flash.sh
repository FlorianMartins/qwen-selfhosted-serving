#!/bin/bash
# Balaye --n-cpu-moe pour qwen-flash ; pour chaque valeur : chargement, bench (PP 8k + TG 1024), pic VRAM
M=/opt/llm/models/qwen3.8-flash-next/UD-Q4_K_XL/Qwen3.8-Flash-Next-UD-Q4_K_XL-00001-of-00004.gguf
for N in "$@"; do
  echo "=== n-cpu-moe=$N"
  "$(dirname "$0")/try.sh" 10002 "${LOG_DIR:-/tmp}/tflash-$N.log" -m $M -ngl 99 --n-cpu-moe $N -t 8 -fa on --jinja -c 65536 -np 1 --fit off \
    --temp 1.0 --top-p 0.95 --top-k 20 --min-p 0.0 --chat-template-kwargs '{"reasoning_effort":"medium"}' || continue
  python3 -I "$(dirname "$0")"/bench.py 10002
  free -g | awk '/Swap/{print "swap used="$3"G"}'
  pgrep -x llama-server | xargs -r kill; sleep 5
done
