# Measurements

Everything below was measured on the production box: **RTX 3090 24 GB** (sm_86, CUDA 13.2),
Ryzen 7 3700X, 128 GB DDR4-3200, llama.cpp built from source, between 2026-10-10 and 2026-10-19.
Model: Qwen3.8-27B (dense) in Unsloth `UD-Q4_K_XL`, unless stated otherwise.
Numbers are single runs on a quiet server; differences under ~3 % are noise.

## 1. Slot layout (before speculative decoding) — `bench/tune_27b_par.sh`

Raw output: [`bench/results-slot-sweep-2026-10-10.log`](../bench/results-slot-sweep-2026-10-10.log).
PP = prompt processing of an 8k-token prompt, TG = generation of 512 tokens per stream.

| config | PP ×1 | TG ×1 | TG ×2 (aggregate) | TG ×4 (aggregate) | VRAM peak |
| --- | --- | --- | --- | --- | --- |
| A `-np 1 -c 65536` (f16 KV) | 1407 tok/s | 39.3 tok/s | — | — | 20.5 GB |
| B `-np 2 -c 131072` KV q8_0 | 1397 | 39.3 | 69.7 | — | 21.4 GB |
| C `-np 4 -c 131072` KV q8_0 | 1384 | 38.2 | 69.5 | 96.6 | 21.6 GB |
| D `-np 4 -kvu -c 131072` KV q8_0 | 1368 | 38.0 | 69.3 | 93.2 | 21.7 GB |
| E as B + `-b 2048 -ub 2048` | 1430 | 39.0 | 69.2 | — | 21.9 GB |

Takeaway: a second slot nearly doubles aggregate throughput for free. Beyond two, each stream
slows to ~25 tok/s, which is too slow for an interactive coding agent.

## 2. Speculative decoding with the built-in MTP head

Qwen3.8-27B ships a multi-token-prediction head (`blk.64.nextn`). `--spec-type draft-mtp`
drafts with it, and the main model verifies every token, so the **output distribution is unchanged**.

| step | code | edit (rewrite a file) | question with thinking |
| --- | --- | --- | --- |
| baseline, 1 slot, no draft | 38.5 tok/s | — | — |
| MTP (test port, `-ctkd/-ctvd q4_0`, 2 × 69632) | 77.8 tok/s | 85.7 tok/s | 30 s → 19.9 s |
| MTP in production | 73.9 tok/s | 86.3 tok/s | 53.3 tok/s |
| MTP + `ngram-mod` drafts | — | **69.3 tok/s** (rejected) | — |

- An MTP draft with a q8_0 cache OOMs at `-c 163840`. With a q4_0 draft cache, `-c 139264` is stable
  (38k-token prompt and 2 concurrent requests). The draft cache only proposes tokens; it never
  decides one.
- Adding `ngram-mod` costs 17 tok/s on edits. MTP alone already gets almost every drafted token
  accepted on that workload, and the long n-gram drafts were mostly rejected.

## 3. Quality: weight and KV-cache quantization — `bench/kld.sh`

KL divergence of the token distribution against a reference (the production weights with an
**f16 KV cache**). Corpus: code + French + English, 3 chunks × 8192 tokens.

| candidate | mean KLD | same top-1 token | decision |
| --- | --- | --- | --- |
| **UD-Q4_K_XL, KV q8_0 / q8_0 (production)** | **0.0021** | **98.1 %** | kept |
| UD-Q4_K_XL, KV q8_0 / **V q4_0** | 0.0032 | 97.7 % | rejected |
| UD-Q4_K_S | 0.0137 | 95.4 % | rejected |
| UD-IQ4_XS | 0.0219 | 94.3 % | rejected |

A q4_0 V cache would have bought 2 × 83968 tokens of context (`-c 167936`, which fits with MTP).
It was turned down: −0.4 point of top-1 agreement on every token, every day, cost more than the
extra context was worth. A smaller weight quant loses 3–4 points, far too much.

A practical lesson: one Q4_K_S download through `HF_XET_HIGH_PERFORMANCE` was silently corrupted.
Verify the sha256 of every GGUF before benchmarking it.

## 4. Context ceiling on 24 GB

| attempt | result |
| --- | --- |
| `-np 2 -c 139264` (2 × 69632) + MTP | stable, VRAM 23.8 / 24.5 GB |
| `-c 147456` | loads, OOMs at run time (~0.5 GB left) |
| `-c 155648` | does not load |
| `-ub 256` | barely helps |
| KV cache in system RAM (`-nkvo`) | 23.6 tok/s short prompt, **6.9 tok/s at 38k** |
| 6 layers offloaded to RAM | 29 tok/s |

The CPU is a Ryzen 7 3700X with DDR4-3200, about 50 GB/s of memory bandwidth, so any offload
costs far more than it gains on this box.

## 5. Shared KV pool (`-kvu`) vs dedicated slots

| test | `-np 3 -kvu -c 122880` | `-np 2 -c 139264` (production) |
| --- | --- | --- |
| 3 short requests in parallel | pass | 2 run, 1 queues |
| one 102k-token prompt alone | pass, 40.9 tok/s | 400 `exceed_context_size` (the client compacts) |
| **two ~70k-token chats at once** | **both fail after 153 s** ("Context size has been exceeded.") | both pass (~59k each: 99 s / 115 s) |

When a shared pool overflows, llama.cpp fails **every** in-flight request, not only the one that
overflowed. On a server where two agents work at once, that failure mode rules `-kvu` out.
(`-c 131072` with `-kvu` loads but OOMs under 3 parallel requests. The MTP context lacks ~760 MB.)

## 6. Parking conversations in RAM (`-cram 49152`)

With two users on one slot, the waiting conversation's KV state is saved to host RAM and
restored when its turn comes. Two 71k-token conversations alternating on one slot came back in
**2.7 s** (20 tokens re-read), instead of **75–147 s** to recompute the prompt cold.

## 7. Reasoning effort and sampling (agent workload)

The Qwen3.8 chat template accepts `xhigh` (the default when nothing is sent), `medium` and `low`;
`none` disables thinking.

| setting, first step of a real agent task | time | reasoning |
| --- | --- | --- |
| `xhigh`, presence_penalty 1.0 | 56–72 s | 10–12k chars |
| `medium`, presence_penalty 0.0 | 8–11 s | same tool calls |

- On Qwen3.8, `low` thinks **longer** than `medium` (3.7–7.4k vs 1.3–3.9k chars on the same step).
- `--reasoning-budget 6144` was removed. Hitting the budget force-closes the thinking block
  mid-plan, which truncated plans and answers.
- presence_penalty went back to the model card's 0.0 (2026-10-14), verified at 78.5 tok/s.
- **Interleaved thinking.** The template renders the previous turns' `reasoning_content` back into
  the prompt. A client that drops that field makes the model re-derive its plan at every agent
  step. Sending it back cut a real 14-module agent run from **166 s to 93 s**.

## 8. Second model: Qwen3.8-Flash-Next (MoE) with experts in RAM

`--n-cpu-moe 40` (experts of the first 40 of 48 layers in system RAM), `-c 65536`:
PP ≈ 185 tok/s on an 8k prompt, TG ≈ 20.8 tok/s, ~21.4 GB VRAM. The model loads on demand
(`ttl 1800`); it serves the "think long, answer later" requests.
