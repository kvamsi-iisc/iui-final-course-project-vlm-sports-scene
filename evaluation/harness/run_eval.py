"""Runs each scenario end to end (Unity Play mode + bridge + local model) and stores one trace per run."""
import argparse, json, os, subprocess, sys, time

S = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "/Users/vakrish3/Documents/IUI/sample final project"
UNITY = "/Applications/Unity/Hub/Editor/6000.6.8f1/Unity.app/Contents/MacOS/Unity"
ANSWERS = ("small|large=>The large one.;;"
           "which of|which locker|which one|exist=>Locker 5 is in locker room B. Please use that one.;;"
           "key=>I don't know where the key is. Search the lockers in locker room A for it.;;"
           "stuck|what should i do|not sure=>Try a different way to finish the task. If there is none, tell me it can't be done.;;"
           "should i|shall i|search|explore=>Yes, go ahead.")
SCENARIOS = [
    ("s1_soccer", "pick up the soccer ball"),
    ("s2_basketball", "put the basketball on the ball rack"),
    ("s3_dumbbell", "hand me the red dumbbell"),
    ("s4_bottle", "put the blue water bottle in the trophy cabinet"),
    ("s5_tennis", "put all three tennis balls on the top shelf of the ball rack"),
    ("s6_goggles", "bring me the swimming goggles"),
    ("s7_medal", "get the gold medal and put it in the trophy cabinet"),
    ("s8_locked", "put the blue dumbbell in locker 5"),
]

p = argparse.ArgumentParser()
p.add_argument("--model", required=True)
p.add_argument("--ollama-url")
p.add_argument("--only", default="")
p.add_argument("--timeout", type=int, default=1500)
p.add_argument("--tag", default="")
p.add_argument("--ollama-timeout")
args = p.parse_args()

for name, command in SCENARIOS:
    if args.only and name not in args.only.split(","):
        continue
    base = f"{S}/eval/runs/{args.model}{args.tag}_{name}"
    for ext in (".jsonl", ".json"):
        if os.path.exists(base + ext):
            os.remove(base + ext)
    cmd = [f"{REPO}/.venv/bin/python", "-u", f"{S}/eval/eval_bridge.py", "--repo", REPO, "--model", args.model, "--out", base + ".jsonl"]
    if args.ollama_url:
        cmd += ["--ollama-url", args.ollama_url]
    if args.ollama_timeout:
        cmd += ["--ollama-timeout", args.ollama_timeout]
    # Batch mode never reaches "end of frame", so the harness copy of the client waits one frame instead.
    cc = f"{S}/UnityCopy/Assets/SportsRobot/Scripts/CommandClient.cs"
    src = open(cc).read()
    if "new WaitForEndOfFrame()" in src:
        open(cc, "w").write(src.replace("yield return new WaitForEndOfFrame();", "yield return null; yield return null;"))
    bridge = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
    bridge.stdout.readline()
    env = dict(os.environ, EVAL_CMD=command, EVAL_ANSWERS=ANSWERS, EVAL_OUT=base + ".json", EVAL_TIMEOUT=str(args.timeout))
    t0 = time.time()
    try:
        subprocess.run([UNITY, "-batchmode", "-projectPath", f"{S}/UnityCopy", "-executeMethod", "BatchEval.Run",
                        "-logFile", base + ".unity.log"], env=env, timeout=args.timeout + 240)
    except subprocess.TimeoutExpired:
        print(name, "unity hung", flush=True)
    bridge.terminate()
    try:
        r = json.load(open(base + ".json"))
        print(f"{name}: {r['outcome']} in {r['seconds']:.0f}s, holding={r['holding']!r}, questions={len(r['questions'])}", flush=True)
    except Exception as e:
        print(name, "no report", e, f"({time.time() - t0:.0f}s)", flush=True)
