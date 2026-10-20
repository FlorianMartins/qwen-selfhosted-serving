# Self-hosted Qwen3.8 on one RTX 3090

[![CI](https://github.com/FlorianMartins/qwen-selfhosted-serving/actions/workflows/ci.yml/badge.svg)](https://github.com/FlorianMartins/qwen-selfhosted-serving/actions/workflows/ci.yml)
[![Security](https://github.com/FlorianMartins/qwen-selfhosted-serving/actions/workflows/security.yml/badge.svg)](https://github.com/FlorianMartins/qwen-selfhosted-serving/actions/workflows/security.yml)
![License](https://img.shields.io/badge/license-MIT-green)

**A production LLM endpoint on a single consumer GPU.** This repository holds the configuration,
the access gateway, the benchmark scripts and the measurements behind each setting. The server
runs Qwen3.8-27B for a team of developers and their coding agents, behind an OpenAI-compatible
API with per-person keys.

One rule held from the start: **no loss of answer quality.** Every speed or context gain was
either lossless by construction or measured against a reference before it shipped. The rejected
options are documented as carefully as the kept ones.

## Results

| | before | after |
| --- | --- | --- |
| generation (code) | 38.5 tok/s | **73.9 – 77.8 tok/s** (lossless MTP speculative decoding) |
| generation (file edits) | — | **86.3 tok/s** |
| parallel requests | 1 | **2 dedicated slots × 69,632 tokens** |
| quality vs f16 KV cache | — | **KLD 0.0021, 98.1 % same top-1 token** |
| resuming a parked 71k-token chat | 75 – 147 s | **2.7 s** |
| first agent step (reasoning effort) | 56 – 72 s | **8 – 11 s**, same tool calls |

Hardware: RTX 3090 24 GB, Ryzen 7 3700X, 128 GB DDR4. Every figure, with the method used and the
negative results, is in [docs/benchmarks.md](docs/benchmarks.md). The reasoning behind each
setting is in [docs/decisions.md](docs/decisions.md).

## Architecture

```
 client (OpenAI SDK, coding agent, curl)
   │  HTTPS, Bearer <personal key>
   ▼
 Caddy ── /v1/* only, TLS, SSE streaming (flush_interval -1, no read timeout)
   │  forward_auth ──► gateway :8090 ── SQLite (SHA-256 key hashes, one-time links)
   │                    ▲  /admin (basic_auth): create · revoke · rotate keys
   │                    └  /claim/<token>: credentials shown once, then erased
   │  Authorization rewritten to the internal key (never handed out)
   ▼
 llama-swap :8080 ── loads one model at a time, on demand
   ├─ qwen-27b    Qwen3.8-27B UD-Q4_K_XL, all in VRAM, 2 slots × 69,632, KV q8_0,
   │              MTP draft head, conversations parked in RAM (-cram)
   └─ qwen-flash  Qwen3.8-Flash-Next (MoE) UD-Q4_K_XL, 40 / 48 layers of experts in RAM
```

Everything listens on 127.0.0.1 except Caddy. The host runs ufw (SSH, 80, 443) and fail2ban
(sshd and the admin panel). Both services run as their own unprivileged users under hardened
systemd units ([deploy/systemd](deploy/systemd)).

## What was tried, and what was kept

- **Kept.** MTP speculative decoding with the model's built-in draft head (×2 throughput, output
  unchanged), a q8_0 KV cache, two dedicated slots, RAM parking of waiting conversations, the
  model card's sampling, and a reasoning effort chosen per request.
- **Rejected after measurement.**
  - V cache q4_0: +14k tokens per slot, but −0.4 pt top-1.
  - Smaller weight quants: −3 to −4 pt.
  - A unified KV pool (`-kvu`): when it overflows, *all* in-flight requests fail.
  - MTP + n-gram drafts: −17 tok/s on edits.
  - A reasoning budget: it truncates plans.
  - KV cache or layers in system RAM: 6.9 tok/s at 38k.
- **Incidents.**
  - An agent "hang" turned out to be client-side `max_tokens` truncation.
  - An internal key leaked to journald through `caddy --environ`; the key was rotated.
  - An OOM from the first MTP attempt led to the rule "spare port first, rollback script ready".

## Repository layout

| path | content |
| --- | --- |
| [`deploy/llama-swap/config.yaml`](deploy/llama-swap/config.yaml) | the production model configuration, commented |
| [`deploy/caddy/Caddyfile`](deploy/caddy/Caddyfile) | TLS reverse proxy, forward_auth, internal key injection |
| [`deploy/systemd/`](deploy/systemd) | hardened units for llama-swap and the gateway |
| [`deploy/env/`](deploy/env) | environment templates (no secrets in the repository) |
| [`gateway/app.py`](gateway/app.py) | access gateway: one file, Python standard library only (admin UI in French) |
| [`tests/`](tests) | end-to-end tests of the gateway (auth, single-use links, revocation, rotation, CSRF) |
| [`bench/`](bench) | throughput, concurrency, context-overflow, tool-calling and KLD scripts |
| [`docs/`](docs) | measurements and decisions |

## Reproduce

```bash
# 1. llama.cpp with CUDA (sm_86 for a 3090)
git clone https://github.com/ggml-org/llama.cpp /opt/llm/llama.cpp
cmake -S /opt/llm/llama.cpp -B /opt/llm/llama.cpp/build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=86
cmake --build /opt/llm/llama.cpp/build -j --target llama-server llama-perplexity

# 2. Models (verify the sha256 of every file: one download here was silently corrupted)
hf download unsloth/Qwen3.8-27B-GGUF --include "*UD-Q4_K_XL*" "mmproj-F16.gguf" --local-dir /opt/llm/models/qwen3.8-27b

# 3. Try a configuration on a spare port before touching production
bench/try.sh 10099 /tmp/try.log -m /opt/llm/models/qwen3.8-27b/Qwen3.8-27B-UD-Q4_K_XL.gguf \
  -ngl 99 -fa on --jinja -np 2 -c 139264 -ctk q8_0 -ctv q8_0 --spec-type draft-mtp -ctkd q4_0 -ctvd q4_0
python3 bench/bench_par.py 10099 1 2          # PP 8k ×1/×2, TG ×1/×2, VRAM peak
python3 bench/chat_tests.py http://127.0.0.1:10099 qwen-27b   # reasoning levels + tool calling
python3 bench/overflow_test.py 10099 2 60000  # two long chats at once
bench/kld.sh corpus.txt ref.gguf candidate.gguf -ctk q8_0 -ctv q8_0   # quality vs f16 KV

# 4. Install deploy/ (llama-swap, Caddy, systemd units, env files from deploy/env/*.example)
```

Gateway tests: `uv sync --locked && uv run pytest -q`.

## CI and supply chain

- **CI**: ruff, pytest, shellcheck, and `caddy validate` with a digest-pinned image.
- **Security baseline** (shared across my repositories): gitleaks, actionlint + zizmor and
  dependency review. Actions are pinned by SHA and checkout credentials are not persisted.
- **Dependencies**: Dependabot with a 7-day cooldown, and a locked `uv.lock`.

## License

MIT
