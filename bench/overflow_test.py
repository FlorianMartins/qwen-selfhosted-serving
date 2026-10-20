# usage: python3 overflow_test.py <port> <n_requests> <prompt_tokens_approx> -> comportement quand la réserve KV partagée sature
import json, sys, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
base = f"http://127.0.0.1:{sys.argv[1]}"; n = int(sys.argv[2]); size = int(sys.argv[3])
para = "The quick brown fox jumps over the lazy dog while the server logs every request with a timestamp and a status code. "
def one(i):
    body = {"messages": [{"role": "user", "content": f"[{i}] " + para * (size // 23) + "\nHow many times does the word fox appear, roughly? Answer in one short sentence."}],
            "max_tokens": 300, "chat_template_kwargs": {"reasoning_effort": "low"}}
    t = time.time()
    try:
        r = json.load(urllib.request.urlopen(urllib.request.Request(base + "/v1/chat/completions", json.dumps(body).encode(), {"Content-Type": "application/json"}), timeout=3600))
        return f"req{i}: OK {time.time()-t:.0f}s prompt={r['usage']['prompt_tokens']} -> {r['choices'][0]['message']['content'].strip()[:50]!r}"
    except urllib.error.HTTPError as e:
        return f"req{i}: HTTP {e.code} {time.time()-t:.0f}s {e.read().decode()[:160]}"
with ThreadPoolExecutor(n) as ex:
    for line in ex.map(one, range(n)): print(line)
