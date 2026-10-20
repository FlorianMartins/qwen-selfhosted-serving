#!/bin/bash
# usage: try.sh <port> <logfile> <args...>  -> lance llama-server (user llm), attend /health, affiche temps de chargement + VRAM
P=$1; L=$2; shift 2
BIN=${LLAMA_SERVER:-/opt/llm/llama.cpp/build/bin/llama-server}
# shellcheck disable=SC2024  # the log is written by the caller (root), the server runs as the unprivileged user
sudo -u llm nohup "$BIN" --host 127.0.0.1 --port $P "$@" > $L 2>&1 &
PID=$!; S=$(date +%s.%N)
until curl -sf 127.0.0.1:$P/health >/dev/null; do
  sleep 0.5
  if ! kill -0 $PID 2>/dev/null; then echo "DIED"; grep -E ' E ' $L | tail -3; exit 1; fi
done
echo "load=$(echo "$(date +%s.%N)-$S" | bc)s"; nvidia-smi --query-gpu=memory.used --format=csv,noheader; free -g | awk '/Mem/{print "RAM used(G)="$3" avail="$7}'
