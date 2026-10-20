# usage: python3 bench.py <port> [reasoning_effort]  -> PP ~8k tokens + génération, via l'API /completion de llama-server
import json, sys, urllib.request, subprocess, threading, time
port = sys.argv[1]
base = f"http://127.0.0.1:{port}"
def post(path, body):
    r = urllib.request.Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=1800))
peak = [0]; stop = [False]
def watch():
    while not stop[0]:
        v = int(subprocess.check_output(["nvidia-smi","--query-gpu=memory.used","--format=csv,noheader,nounits"]).decode().strip())
        peak[0] = max(peak[0], v); time.sleep(0.5)
threading.Thread(target=watch, daemon=True).start()
para = "The quick brown fox jumps over the lazy dog while the server logs every request with a timestamp and a status code. "
text = para * 360   # ~8000 tokens
n = len(post("/tokenize", {"content": text})["tokens"])
r = post("/completion", {"prompt": text + "\nSummarize the text above in one sentence.", "n_predict": 1, "cache_prompt": False})
t = r["timings"]; print(f"prompt_tokens={t['prompt_n']} PP={t['prompt_per_second']:.1f} tok/s (tokenized {n})")
r = post("/completion", {"prompt": "Write a long detailed essay about the history of computing.", "n_predict": 1024, "ignore_eos": True, "cache_prompt": False})
t = r["timings"]; print(f"gen_tokens={t['predicted_n']} TG={t['predicted_per_second']:.2f} tok/s")
stop[0] = True; print(f"VRAM peak={peak[0]} MiB")
