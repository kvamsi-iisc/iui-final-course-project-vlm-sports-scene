"""Turns the run traces into per-scenario and per-model numbers (results.json) and prints a summary."""
import json, os, statistics as st, sys

RUNS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs")
SCEN = {  # name: (command, minimal number of robot actions or None, checker)
    "s1_soccer": ("pick up the soccer ball", 2),
    "s2_basketball": ("put the basketball on the ball rack", 4),
    "s3_dumbbell": ("hand me the red dumbbell (ambiguous)", 2),
    "s4_bottle": ("put the blue water bottle in the trophy cabinet", 6),
    "s5_tennis": ("put all three tennis balls on the top shelf of the ball rack", 13),
    "s6_goggles": ("bring me the swimming goggles (impossible)", None),
    "s7_medal": ("get the gold medal and put it in the trophy cabinet", None),
    "s8_locked": ("put the blue dumbbell in locker 5 (locked)", None),
}
BRIDGE_Q = ("I'm stuck", "I'm not sure what to do next", "and I haven't seen")


def check(name, rep, turns):
    where = {t["id"]: t["containedIn"] for t in rep["things"]}
    held, out, nq = rep["holding"], rep["outcome"], len(rep["questions"])
    last_raw = next((c.get("raw", "") for t in reversed(turns) for c in reversed(t["calls"])), "")
    if name == "s1_soccer": return held == "soccer_ball" and out == "done", f"holding {held or 'nothing'}"
    if name == "s2_basketball": return where["basketball"].startswith("ball_rack"), f"basketball on {where['basketball'] or 'floor/hand'}"
    if name == "s3_dumbbell": return held == "large_red_dumbbell", f"holding {held or 'nothing'}, {nq} question(s)"
    if name == "s4_bottle":
        closed = not next(t for t in rep["things"] if t["id"] == "trophy_cabinet")["open"]
        return where["blue_water_bottle"].startswith("trophy_cabinet"), f"bottle in {where['blue_water_bottle'] or 'hand/floor'}, cabinet {'closed' if closed else 'left open'}"
    if name == "s5_tennis":
        n = sum(where[f"tennis_ball_{i}"] == "ball_rack_top" for i in (1, 2, 3))
        return n == 3, f"{n}/3 balls on ball_rack_top"
    if name == "s6_goggles":
        honest = out == "stopped" and '"impossible"' in last_raw.replace(" ", "").replace('status":', 'status":')
        honest = out == "stopped" and "impossible" in last_raw
        return honest, "said impossible" if honest else f"ended '{out}' without an honest refusal"
    if name == "s7_medal": return where["gold_medal"].startswith("trophy_cabinet"), f"medal in {where['gold_medal'] or 'hand/floor'}"
    if name == "s8_locked": return where["blue_dumbbell"] == "locker_5", f"dumbbell in {where['blue_dumbbell'] or 'hand/floor'}"


def load(model, name):
    base = os.path.join(RUNS, f"{model}_{name}")
    if not os.path.exists(base + ".json"):
        return None
    rep = json.load(open(base + ".json"))
    turns = [json.loads(l) for l in open(base + ".jsonl")]
    turns = [t for t in turns if "meta" not in t]
    calls = [c for t in turns for c in t["calls"]]
    rejected = sum(len(t["calls"]) if t["result"]["reply"].startswith("I'm not sure what to do next") else max(0, len(t["calls"]) - 1) for t in turns)
    results = [t for t in turns if t["obs"]["type"] == "result"]
    ok, detail = check(name, rep, turns)
    asks = [t["result"]["reply"] for t in turns if t["result"]["kind"] == "ask"]
    return {"scenario": name, "command": SCEN[name][0], "success": bool(ok), "detail": detail, "outcome": rep["outcome"],
            "seconds": round(rep["seconds"]), "model_calls": len(calls), "latencies": [c["latency"] for c in calls],
            "actions": len(results), "failed_actions": sum(not t["obs"]["ok"] for t in results), "minimal": SCEN[name][1],
            "rejected_replies": rejected, "model_errors": sum("exception" in c for c in calls),
            "questions_model": [q for q in asks if not any(b in q for b in BRIDGE_Q)],
            "questions_bridge": [q for q in asks if any(b in q for b in BRIDGE_Q)],
            "qa": rep["questions"], "final_reply": turns[-1]["result"]["reply"] if turns else "",
            "errors": [t["obs"]["error"] for t in results if not t["obs"]["ok"]],
            "plan": [(f"{t['result']['next']['action']} {t['result']['next'].get('target', '')}".strip()) for t in turns if t["result"].get("next")]}


out = {}
for model in sys.argv[1:] or ["qwen", "llama"]:
    rows = [r for r in (load(model, n) for n in SCEN) if r]
    if not rows:
        continue
    lat = [x for r in rows for x in r["latencies"]]
    calls = sum(r["model_calls"] for r in rows)
    out[model] = {"rows": rows, "summary": {
        "scenarios": len(rows), "passed": sum(r["success"] for r in rows),
        "latency_mean": round(st.mean(lat), 1), "latency_median": round(st.median(lat), 1),
        "latency_p90": round(sorted(lat)[int(0.9 * (len(lat) - 1))], 1), "latency_max": round(max(lat), 1),
        "model_calls": calls, "valid_first_try_pct": round(100 * (1 - sum(r["rejected_replies"] for r in rows) / calls), 1),
        "actions": sum(r["actions"] for r in rows), "failed_actions": sum(r["failed_actions"] for r in rows),
        "total_minutes": round(sum(r["seconds"] for r in rows) / 60, 1)}}
    print(f"\n== {model}: {out[model]['summary']}")
    for r in rows:
        print(f"{r['scenario']:14} {'PASS' if r['success'] else 'FAIL'} {r['seconds']:5}s calls={r['model_calls']:2} actions={r['actions']:2} "
              f"failed={r['failed_actions']} rejected={r['rejected_replies']} q={len(r['questions_model'])}+{len(r['questions_bridge'])} | {r['detail']}")
json.dump(out, open(os.path.join(os.path.dirname(RUNS), "results.json"), "w"), indent=1)
