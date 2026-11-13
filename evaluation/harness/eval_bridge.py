"""Evaluation bridge: the project's own Brain and backend, plus timing and a JSONL trace of every turn."""
import argparse, asyncio, json, sys, time

p = argparse.ArgumentParser()
p.add_argument("--repo", required=True)
p.add_argument("--model", required=True)
p.add_argument("--port", type=int, default=8766)
p.add_argument("--ollama-url")
p.add_argument("--out", required=True)
p.add_argument("--ollama-timeout")
args = p.parse_args()
sys.path.insert(0, args.repo)
import vlm_bridge as vb
import websockets

props = vb.load_properties(args.repo + "/vlm.properties")
props["backend"] = "ollama"
props["ollama.model"] = args.model
if args.ollama_url:
    props["ollama.url"] = args.ollama_url
if args.ollama_timeout:
    props["ollama.timeout"] = args.ollama_timeout
backend = vb.make_backend(props)
feedback = props.get("feedback", "every_step")
out = open(args.out, "a", encoding="utf-8")
calls = []


def emit(rec):
    out.write(json.dumps(rec) + "\n")
    out.flush()


def timed(system, prompt, images):
    t0 = time.monotonic()
    rec = {"prompt_chars": len(system) + len(prompt), "images": len(images)}
    try:
        raw = backend(system, prompt, images)
        rec["raw"] = raw
        return raw
    except Exception as e:
        rec["exception"] = str(e)
        raise
    finally:
        rec["latency"] = round(time.monotonic() - t0, 2)
        calls.append(rec)


async def handler(ws):
    brain = vb.Brain(timed, feedback=feedback, log=lambda *a: None)
    async for message in ws:
        obs = json.loads(message)
        if obs.get("type") in ("command", "result") and obs.get("image_b64"):   # keep what the model saw, for checking
            import base64
            n = handler.count = getattr(handler, "count", 0) + 1
            if n <= 3:
                for key in ("image_b64", "overhead_b64"):
                    open(args.out.replace(".jsonl", f".{n}.{key[:-4]}.jpg"), "wb").write(base64.b64decode(obs[key]))
        calls.clear()
        t0 = time.monotonic()
        result = await asyncio.to_thread(brain.handle, obs)
        emit({"ts": round(time.time(), 2), "obs": {k: obs.get(k) for k in ("type", "text", "ok", "error", "note", "newly_seen", "holding", "location")},
              "n_known": len(obs.get("known") or []), "turn_seconds": round(time.monotonic() - t0, 2), "calls": list(calls),
              "result": {k: result.get(k) for k in ("kind", "reply", "error", "next", "step", "failures")}})
        await ws.send(json.dumps(result))


async def main():
    emit({"meta": {"model": backend.model, "feedback": feedback, "name": getattr(backend, "name", "")}})
    async with websockets.serve(handler, "localhost", args.port, max_size=None):
        print("ready", flush=True)
        await asyncio.Future()

asyncio.run(main())
