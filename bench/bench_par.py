# usage: python3 bench_par.py <port> <N concurrents...> -> débit agrégé de génération (N flux x 512 tokens) + PP 8k (1 et 2 en parallèle) + pic VRAM
import json, sys, subprocess, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
base = f"http://127.0.0.1:{sys.argv[1]}"
def post(body):
    r = urllib.request.Request(base + "/completion", json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=3600))
peak = [0]; stop = [False]
def watch():
    while not stop[0]:
        peak[0] = max(peak[0], int(subprocess.check_output(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"]).decode()))
        time.sleep(0.3)
threading.Thread(target=watch, daemon=True).start()
para = "The quick brown fox jumps over the lazy dog while the server logs every request with a timestamp and a status code. "
def pp(i):
    return post({"prompt": f"[{i}] " + para * 360 + "\nSummarize.", "n_predict": 1, "cache_prompt": False})["timings"]
for n in (1, 2):
    t = time.time()
    with ThreadPoolExecutor(n) as ex: r = list(ex.map(pp, range(n)))
    tok = sum(x["prompt_n"] for x in r); print(f"PP x{n}: {tok/(time.time()-t):.0f} tok/s agrégé ({tok} tokens)")
def gen(i):
    return post({"prompt": f"Essay #{i}: write a long detailed history of computing.", "n_predict": 512, "ignore_eos": True, "cache_prompt": False})["timings"]
for n in map(int, sys.argv[2:]):
    t = time.time()
    with ThreadPoolExecutor(n) as ex: r = list(ex.map(gen, range(n)))
    dt = time.time() - t; tok = sum(x["predicted_n"] for x in r)
    print(f"TG x{n}: {tok/dt:.1f} tok/s agrégé | par flux {min(x['predicted_per_second'] for x in r):.1f}-{max(x['predicted_per_second'] for x in r):.1f} tok/s")
stop[0] = True; print(f"VRAM pic={peak[0]} MiB")
