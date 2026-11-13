import json, sys
for name in sys.argv[1:]:
    print("=====", name)
    for l in open(f"{sys.path[0]}/runs/{name}.jsonl"):
        t = json.loads(l)
        if "meta" in t: continue
        o, r = t["obs"], t["result"]
        head = o["type"] + (f" '{o['text']}'" if o.get("text") else "") + ("" if o.get("ok", True) else f" FAILED: {o['error']}") + (f" ({o['note']})" if o.get("note") else "")
        print(f"< {head[:150]}")
        for c in t["calls"]:
            print(f"   [{c['latency']}s] {c.get('raw', c.get('exception', ''))[:330]}")
        nxt = r.get("next")
        print(f"> {r['kind']}: {(nxt['action'] + ' ' + nxt.get('target', '')) if nxt else r['reply'][:200]}" + (f"  !! {r['error'][:100]}" if r["error"] else ""))
