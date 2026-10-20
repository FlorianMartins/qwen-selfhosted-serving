#!/bin/bash
# usage: kld.sh <corpus.txt> <reference.gguf> <candidate.gguf> [candidate KV flags...]
#   -> KL divergence and top-1 agreement of a candidate (weight quant and/or KV-cache quant) against a reference.
# Method of docs/kld-study.md: the reference is the production weights with an f16 KV cache, the corpus mixes
# code, French and English, and 3 chunks of 8192 tokens are scored (-c 8192 --chunks 3).
# Example (V cache q4_0 vs reference):  kld.sh corpus.txt Q4_K_XL.gguf Q4_K_XL.gguf -ctk q8_0 -ctv q4_0
set -euo pipefail
CORPUS=$1; REF=$2; CAND=$3; shift 3
BIN=${LLAMA_PERPLEXITY:-/opt/llm/llama.cpp/build/bin/llama-perplexity}
BASE=${KLD_BASE:-/tmp/kld-ref.bin}
COMMON=(-ngl 99 -fa on -c 8192 --chunks 3 -f "$CORPUS")
if [ ! -s "$BASE" ]; then
  "$BIN" -m "$REF" "${COMMON[@]}" -ctk f16 -ctv f16 --kl-divergence-base "$BASE"
fi
"$BIN" -m "$CAND" "${COMMON[@]}" "$@" --kl-divergence-base "$BASE" --kl-divergence 2>&1 |
  grep -E "Mean    KLD|Same top p|Mean PPL\(Q\)"
