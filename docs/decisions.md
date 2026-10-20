# Decisions

Each line of the [llama-swap configuration](../deploy/llama-swap/config.yaml) is a decision.
This page records why, including the options that were measured and **rejected**. One rule was
fixed before any tuning: **no loss of answer or reasoning quality, ever.** Speed and context are
only bought with lossless techniques or with quality measured as equivalent.

| # | decision | why | rejected alternatives |
| --- | --- | --- | --- |
| 1 | llama.cpp + llama-swap | GGUF Q4 + q8_0 KV fits the 27B model and 2 × 69k tokens of context on one 24 GB card, supports the model's MTP head, and llama-swap loads one model at a time on demand | other engines were not benchmarked here |
| 2 | Unsloth `UD-Q4_K_XL` | the most accurate 4-bit quant tested: the smaller ones lose 3–4 points of top-1 agreement | Q4_K_S (95.4 %), IQ4_XS (94.3 %) — [measurements](benchmarks.md#3-quality-weight-and-kv-cache-quantization--benchkldsh) |
| 3 | KV cache q8_0 / q8_0 | KLD 0.0021 / 98.1 % top-1 against an f16 cache, for half its memory | V q4_0: +14k tokens per slot for −0.4 pt top-1 |
| 4 | MTP speculative decoding, `--spec-draft-n-max 3`, q4_0 draft cache | ×2 generation speed, lossless by construction (the main model verifies) | `draft-mtp,ngram-mod`: −17 tok/s on edits; DFlash2: no VRAM left |
| 5 | 2 dedicated slots × 69632 | two agents (or sub-agents) run in parallel; an overflow only hurts its own request | `-np 1` (no parallelism); `-np 3 -kvu` (two long chats both fail) |
| 6 | `-cram 49152` | a parked conversation comes back in 2.7 s instead of 75–147 s | — |
| 7 | official sampling, presence_penalty 0.0 | the model card warns that high values cause language mixing | presence 1.0 (used 2026-10-11 → 10-14) |
| 8 | no `--reasoning-budget` | the budget cuts thinking mid-plan | 6144 tokens: truncated answers |
| 9 | the client chooses the reasoning effort | `medium` gives the same agent actions in ~1/6 of the time; `xhigh` stays available | — |
| 10 | Caddy in front, `/v1/*` only | TLS, SSE streaming (`flush_interval -1`, no read timeout), forward_auth | exposing llama-swap directly |
| 11 | own gateway for keys | per-person/project keys, SHA-256 hashes only, single-use credential links, revocation and rotation; the internal key never leaves the server | one shared key for everybody |
| 12 | stay on the 3090 | a power limit of 371 W would gain a few % at most (the card is already power/thermal bound) | — the only real step up is a bigger GPU |

## Incidents that shaped the configuration

- **"The agent hangs."** The server was healthy: no swaps, no OOM, no 5xx. The replies were hitting
  the client's `max_tokens` 8192 at ~29 tok/s (≈ 4.7 min), and the client silently treated them
  as finished. The fix went into the client, and the server got `--metrics` and journald logs
  so the next diagnosis takes minutes.
- **Secret in the journal.** Caddy had been started with `--environ`, which prints the whole
  environment, internal key included, to journald. The flag was removed and the key rotated
  (old key → 401). The environment backups that held the old key were shredded.
- **MTP OOM in production.** The first MTP attempt with a q8_0 draft cache at `-c 163840` took the
  model down. Since then every change is tested on a spare port first, with
  [`bench/try.sh`](../bench/try.sh), and a rollback script is written before the switch.
