# usage: python3 chat_tests.py <base_url> <model> [api_key]  -> tests reasoning_effort low/high + tool calling
import json, sys, time, urllib.request
base, model = sys.argv[1], sys.argv[2]
hdr = {"Content-Type": "application/json"}
if len(sys.argv) > 3: hdr["Authorization"] = "Bearer " + sys.argv[3]
def chat(body):
    body["model"] = model
    r = urllib.request.Request(base + "/v1/chat/completions", json.dumps(body).encode(), hdr)
    t = time.time(); d = json.load(urllib.request.urlopen(r, timeout=1800)); return d, time.time() - t
q = [{"role": "user", "content": "A bat and a ball cost 1.10 in total. The bat costs 1.00 more than the ball. How much does the ball cost? Answer briefly."}]
for eff in ("low", "high"):
    d, dt = chat({"messages": q, "max_tokens": 8000, "chat_template_kwargs": {"reasoning_effort": eff}})
    m = d["choices"][0]["message"]
    print(f"[{eff}] reasoning_chars={len(m.get('reasoning_content') or '')} completion_tokens={d['usage']['completion_tokens']} t={dt:.1f}s answer={m['content'].strip()[:80]!r}")
tools = [{"type": "function", "function": {"name": "get_weather", "description": "Get current weather for a city",
          "parameters": {"type": "object", "properties": {"city": {"type": "string"}, "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}}, "required": ["city"]}}}]
d, dt = chat({"messages": [{"role": "user", "content": "What's the weather in Brussels in celsius? Use the tool."}], "tools": tools, "max_tokens": 4000,
              "chat_template_kwargs": {"reasoning_effort": "low"}})
c = d["choices"][0]; tc = c["message"].get("tool_calls")
ok = bool(tc) and tc[0]["function"]["name"] == "get_weather" and "city" in json.loads(tc[0]["function"]["arguments"])
print(f"[tools] finish_reason={c['finish_reason']} ok={ok} tool_calls={json.dumps(tc)[:200]}")
