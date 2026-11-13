"""
VLM Bridge  —  Unity  <-- WebSocket -->  this script  <-- HTTP -->  vision model
                                                                   (Ollama by default; OpenRouter optional)

Closed loop, one action at a time:
  1. Unity sends the user's command plus what the robot perceives right now: its eye camera, the
     overhead camera, the objects it has seen so far (with their state), what it holds, where it is.
  2. The model answers with JSON: a status, a sentence for the user and the remaining plan.
     We validate it and send Unity only the FIRST action.
  3. Unity runs it and reports the result with fresh images. Back to 2, until the model says the task
     is done, asks the user a question, or a limit is hit (40 actions, 5 recoveries from failures).

Choose ollama.model = qwen | llama in vlm.properties (backend = ollama | openrouter).

Run:
  pip install -r requirements.txt
  python tools/run_local.py                    # start the compatible Ollama runtime and bridge
  python vlm_bridge.py                          # serve Unity with the settings in vlm.properties
  python vlm_bridge.py --backend ollama         # override the backend (or --model, --feedback) for one run
  python vlm_bridge.py --test "put the basketball on the ball rack" [--image eye.jpg] [--overhead top.jpg]
  python vlm_bridge.py --selftest               # check the loop logic without any model or Unity
"""
import argparse
import asyncio
import base64
import io
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request

HOST, PORT = "localhost", 8765
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MAX_STEPS = 40       # actions per command (a room search alone takes about six)
MAX_FAILURES = 5     # failed steps or unusable replies we recover from before giving up
STUCK_AFTER = 3      # failed actions in a row before the robot stops trying and asks the user what to do

OLLAMA_MODELS = {"qwen": "qwen3-vl:8b-instruct", "llama": "llama3.2-vision:11b"}

# Command vocabulary: action name -> which parameter it needs (None = no parameter, target optional)
ACTIONS = {
    "move_to": "target",
    "pick": "target",
    "place": "target",
    "open": "target",
    "close": "target",
    "rotate": "degrees",
    "wait": "seconds",
    "look_around": None,
    "explore": None,
}

SYSTEM_PROMPT = """You are the brain of a mobile robot working inside a building. The user gives a task in everyday
language. You decide what the robot does. The robot has an eye camera and ONE hand: it carries one thing at a time.

EVERY TURN YOU GET
- Image 1: the robot's eye camera (what it is looking at now). Image 2: a camera above the building, north at the
  top, east on the right; the robot is the small round white shape and its orange tray marks its front. Rooms
  with a roof can't be seen from above.
- TASK, and the user's answers to questions you asked earlier.
- ROBOT: where it is and what it holds.
- KNOWN OBJECTS: everything the robot has really seen so far, with its state and place. Rooms and doors are always
  known. The building contains more things than these; the rest has simply not been seen yet.
- NOT EXPLORED YET: rooms never entered and places never looked into.
- ACTIONS SO FAR with their results, and GOAL CHECK: your goal compared with the real state of the world.

ACTIONS
  {"action": "move_to", "target": "<id>"}   walk to an object, or into a room
  {"action": "pick", "target": "<id>"}      take a small object in the hand (the robot walks to it by itself)
  {"action": "place", "target": "<id>"}     put the held object on or in <id> (walks there by itself). Name the
                                            rack or cabinet itself and a free shelf is chosen; name one shelf
                                            (its own id) only when the user asks for that shelf
  {"action": "open", "target": "<id>"}      a door, locker or cabinet
  {"action": "close", "target": "<id>"}
  {"action": "look_around"}                 turn a full circle and notice everything in sight from here
  {"action": "explore", "target": "<id>"}   search a place. A room: go in (its door is opened for you), look
                                            around, and look into everything there that opens without a key.
                                            A floor area: look closely at every piece of furniture in it.
                                            A piece of furniture (shelf, cart, locker): look into it up close.
  {"action": "rotate", "degrees": <n>}      turn in place, positive = right
  {"action": "wait", "seconds": <n>}

REPLY WITH ONLY THIS JSON, the keys in this order:
{"things": [<each thing the user mentions, with EVERY known id that fits the words>],
 "goal": [<what must be true when the task is finished>],
 "status": "working" | "done" | "ask" | "impossible",
 "reply": "<one short sentence for the user>",
 "question": "<only when status is ask>",
 "plan": [<ALL remaining steps, in order>]}

THINGS: for each object or place in the task, {"words": "<the user's words>", "ids": [<ALL ids in KNOWN OBJECTS that
fit those words; [] if none is known yet>]}. List every id that fits, not only the one you would choose: for "the
green mug" with a tall and a short green mug known, ids is ["tall_green_mug", "short_green_mug"].

GOAL: one entry for each thing the task is about, in one of these forms:
  {"holding": "<id>"}                 the robot ends up holding it (bring me, fetch, hand me, pick up, find me ...)
  {"object": "<id>", "in": "<id>"}    the object ends up on or in that place (a shelf, rack, locker, cabinet, mat, room ...)
  {"open": "<id>"}  or  {"closed": "<id>"}
  {"robot_at": "<id>"}                the robot itself ends up next to that thing or inside that room or area
Say only how things must be at the END. "get X and put it in Y", "take X to Y", "move X onto Y" are all just
{"object": X, "in": Y}: the robot does not hold X at the end. Use "holding" only when the user wants X handed over.
State the same goal every turn until it is reached. For a thing that is in KNOWN OBJECTS use its exact id. For a
thing that has not been seen yet, write the id you expect it to have, in the same style (gold_medal, locker_5).
Use [] only when none of the forms fits the task (for example "turn around").

HOW TO DECIDE
1. Understand the task. Match the user's words to KNOWN OBJECTS by MEANING, not by spelling: another word for the
   same thing, a description (colour, size, position) or a number all count, and the images help with colour and
   size. But a different object is never a substitute: something similar, or the same kind of thing with another
   name or number, is NOT what the user asked for.
2. Look at the ids you listed under "things". One id for a thing: use it. Two or more ids for ONE thing the user
   asked for in the singular: do NOT choose one yourself. Return status "ask" with one short question naming the
   choices, and an empty plan. If the user asked for all of them or for a number of them, or has already told you
   which one, there is nothing to ask. Never ask when only one object fits.
3. If something the task needs is not in KNOWN OBJECTS, FIND IT FIRST, with an empty hand, before you pick up or
   move anything else:
   - begin with look_around (quick), unless the robot has already looked around where it stands;
   - then explore the places where a thing like that is normally kept, most likely place first and the nearer one
     first when they are equally likely. Only places under NOT EXPLORED YET can still hide something. If it is
     not where you expected, go on to the other unexplored rooms and closed lockers or cupboards;
   - after the search steps, write the rest of the plan using the id you expect. Steps that name a thing not seen
     yet wait: the search ends and the plan goes on at the moment that thing is seen.
   Never ask the user for permission to look or explore.
4. Plan the shortest way to the goal that respects the world:
   - one thing in the hand; to take a second thing, put the first down on a surface nearby and fetch it again later;
   - a closed locker or cabinet must be opened before taking things out or putting things in; close a cabinet again
     when you have finished with it;
   - a locked thing ("locked, needs X") opens only while the robot HOLDS X. The key stays in the lock, so the hand
     is free afterwards. If X is in KNOWN OBJECTS, fetch it. If X has not been seen, return status "ask": say what
     is locked and which key is missing, and ask whether the user knows where it is or wants you to search;
   - a closed door blocks the way into its room: open it first (explore does this itself).
5. When a step failed, read the error and fix its cause with a different step. Never repeat a failed step unchanged
   and never repeat a step that has already succeeded. If nothing fixes it, return status "ask".
6. Return status "done" with an empty plan only when GOAL CHECK shows every part of the goal as REACHED.
7. Return status "impossible" with an empty plan, and say why, when the task makes no sense or can't be done, or
   when the thing is still unknown after every sensible place has been explored. Don't give up while a sensible
   place is still listed under NOT EXPLORED YET, and never report success for something that was not done.

EXAMPLES (from another building: their ids do not exist here)
TASK: put the stapler in the top drawer. KNOWN OBJECTS include stapler and desk_drawer_top (closed).
{"things": [{"words": "the stapler", "ids": ["stapler"]}, {"words": "the top drawer", "ids": ["desk_drawer_top"]}],
 "goal": [{"object": "stapler", "in": "desk_drawer_top"}, {"closed": "desk_drawer_top"}], "status": "working",
 "reply": "Putting the stapler in the top drawer.", "plan": [{"action": "pick", "target": "stapler"},
 {"action": "open", "target": "desk_drawer_top"}, {"action": "place", "target": "desk_drawer_top"},
 {"action": "close", "target": "desk_drawer_top"}]}

TASK: bring me my passport. No passport is known; papers like that are kept in the office or the bedroom safe.
{"things": [{"words": "my passport", "ids": []}],
 "goal": [{"holding": "passport"}], "status": "working", "reply": "I haven't seen a passport yet, so I'll search for it.",
 "plan": [{"action": "look_around"}, {"action": "explore", "target": "office"}, {"action": "explore", "target": "bedroom"},
 {"action": "pick", "target": "passport"}]}

TASK: hand me the green mug. KNOWN OBJECTS include tall_green_mug and short_green_mug.
{"things": [{"words": "the green mug", "ids": ["tall_green_mug", "short_green_mug"]}],
 "goal": [], "status": "ask", "reply": "There are two green mugs.", "question": "Do you want the tall or the short green mug?", "plan": []}
"""


# ---------------------------------------------------------------- settings

def load_properties(path):
    """Reads "key = value" lines; "#" starts a comment."""
    props = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if "=" in line:
                key, value = line.split("=", 1)
                props[key.strip()] = value.strip()
    return props


def openrouter_key(props):
    """The key comes from the environment, or from the key file; it is never stored in code."""
    env = props.get("openrouter.key_env", "OPENROUTER_API_KEY")
    if os.environ.get(env, "").strip():
        return os.environ[env].strip()
    path = props.get("openrouter.key_file", "")
    if path:
        path = path if os.path.isabs(path) else os.path.join(BASE_DIR, path)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                m = re.search(r"sk-or-[A-Za-z0-9_-]+", f.read())
            if m:
                return m.group(0)
    raise SystemExit(f"No OpenRouter API key found: set {env}, or put the key in "
                     f"openrouter.key_file ({props.get('openrouter.key_file', 'not set')}).")


# ---------------------------------------------------------------- model backends
# Each backend is called as backend(system_prompt, user_text, images_base64) and returns raw text.

class ModelError(Exception):
    pass


def combine_camera_images(images):
    """Keep both camera views for models that accept only one image per request."""
    try:
        from PIL import Image, ImageDraw
    except ImportError as e:
        raise ModelError("Llama's two-camera input needs Pillow: python3 -m pip install Pillow") from e
    if len(images) != 2:
        raise ModelError("Expected exactly two camera images")
    panels = []
    try:
        for encoded in images:
            if len(encoded) > 12_000_000:
                raise ValueError("camera image exceeds size limit")
            with Image.open(io.BytesIO(base64.b64decode(encoded, validate=True))) as img:
                if img.width * img.height > 16_000_000:
                    raise ValueError("camera image exceeds pixel limit")
                img.thumbnail((960, 720))
                panels.append(img.convert("RGB"))
    except (ValueError, OSError, Image.DecompressionBombError) as e:
        raise ModelError(f"Invalid camera image: {e}") from e
    header, gap = 28, 12
    canvas = Image.new("RGB", (sum(p.width for p in panels) + gap,
                               max(p.height for p in panels) + header), "white")
    draw = ImageDraw.Draw(canvas)
    x = 0
    for label, panel in zip(("Image 1: robot eye", "Image 2: overhead"), panels):
        draw.text((x + 4, 6), label, fill="black")
        canvas.paste(panel, (x, header))
        x += panel.width + gap
    output = io.BytesIO()
    canvas.save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


class OllamaBackend:
    def __init__(self, url, model, timeout=300, num_ctx=8192, keep_alive="5m", num_batch=128, combine=False):
        self.url, self.model = url, model
        # Llama takes one image per request. For other models merging is a speed choice: each image costs
        # the model about as much time as all of the text together.
        self.combine = combine or model.split(":", 1)[0] == "llama3.2-vision"
        self.name = f"ollama:{model}"
        self.timeout, self.num_ctx, self.keep_alive = timeout, num_ctx, keep_alive
        self.num_batch = num_batch

    def __call__(self, system, user_text, images):
        if self.combine and len(images) > 1:
            images = [combine_camera_images(images)]
            system += ("\nThe two camera views are combined into ONE image: the LEFT panel is Image 1 "
                       "(robot eye), and the RIGHT panel is Image 2 (overhead).")
        user = {"role": "user", "content": user_text}
        if images:
            user["images"] = images
        body = {
            "model": self.model,
            "stream": False,
            "format": "json",                                  # forces valid JSON output
            "keep_alive": self.keep_alive,
            "options": {"temperature": 0, "num_ctx": self.num_ctx,
                        "num_batch": self.num_batch, "num_predict": 1000},
            "messages": [{"role": "system", "content": system}, user],
        }
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.load(resp)
            if data.get("error"):
                raise ModelError(f"Ollama: {data['error']}")
            self.last_stats = {k: data.get(k) for k in ("load_duration", "prompt_eval_count", "prompt_eval_duration",
                                                        "eval_count", "eval_duration", "total_duration")}
            text = (data.get("message") or {}).get("content")
            if not isinstance(text, str) or not text.strip():
                raise ModelError("Ollama returned an empty reply")
            return text
        except urllib.error.HTTPError as e:
            detail = error_text(e)
            if e.code == 404:
                raise ModelError(f"Ollama model {self.model!r} is unavailable; "
                                 f"run: bash tools/ollama.sh pull {self.model}. {detail}") from e
            if "unknown model architecture" in detail and "mllama" in detail:
                raise ModelError("This Ollama runtime cannot load Llama 3.2 Vision. "
                                 "Stop it and use: .venv/bin/python tools/run_local.py --model llama") from e
            raise ModelError(f"Ollama HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise ModelError(f"can't reach Ollama at {self.url} ({e.reason}); is it running? "
                             "Start the local stack with: .venv/bin/python tools/run_local.py") from e
        except (TimeoutError, socket.timeout) as e:
            raise ModelError(f"Ollama timed out after {self.timeout}s; increase ollama.timeout "
                             "in vlm.properties or use a smaller model") from e
        except (ValueError, AttributeError, TypeError) as e:
            raise ModelError("Ollama returned an invalid chat response") from e


class OpenRouterBackend:
    def __init__(self, url, model, key, fallbacks=(), app_name="", retries=3):
        self.url, self.model, self.fallbacks = url, model, [m for m in fallbacks if m]
        self.key, self.app_name, self.retries = key, app_name, retries
        self.json_mode = True
        self.name = f"openrouter:{model}"

    def __call__(self, system, user_text, images):
        content = [{"type": "text", "text": user_text}]
        content += [{"type": "image_url", "image_url": {"url": data_url(b)}} for b in images]
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
            "temperature": 0,
            "max_tokens": 1000,
        }
        if self.fallbacks:
            body["models"] = [self.model] + self.fallbacks
        for attempt in range(self.retries + 1):
            if self.json_mode:
                body["response_format"] = {"type": "json_object"}
            else:
                body.pop("response_format", None)
            try:
                return self._post(body)
            except urllib.error.HTTPError as e:
                detail = error_text(e)
                if e.code == 400 and self.json_mode and "response_format" in detail.lower():
                    self.json_mode = False          # this model can't do JSON mode; the prompt still asks for JSON
                    continue
                if e.code == 401:
                    raise ModelError("OpenRouter rejected the API key (401); check creds/openRouter.txt") from e
                if e.code == 402:
                    raise ModelError(f"{self.model} needs OpenRouter credits (402). Free-tier keys can only "
                                     f"use models ending in ':free'.") from e
                if e.code == 429 and "per-day" in detail.lower():
                    raise ModelError("OpenRouter's daily limit for free models is used up (50 requests a day "
                                     "on the free tier). Try again tomorrow, use feedback = after_plan, "
                                     "or switch to backend = ollama.") from e
                if e.code in (408, 429, 500, 502, 503, 504) and attempt < self.retries:
                    wait = retry_after(e) or 2 ** (attempt + 1)
                    print(f"  OpenRouter busy (HTTP {e.code}), retrying in {wait:.0f} s")
                    time.sleep(wait)
                    continue
                raise ModelError(f"OpenRouter HTTP {e.code}: {detail}") from e
            except urllib.error.URLError as e:
                if attempt < self.retries:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise ModelError(f"can't reach OpenRouter ({e.reason})") from e
        raise ModelError("OpenRouter kept failing")

    def _post(self, body):
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"}
        if self.app_name:
            headers["X-Title"] = self.app_name
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.load(resp)
        if data.get("error"):
            raise ModelError(f"OpenRouter error: {data['error'].get('message', data['error'])}")
        msg = data["choices"][0]["message"]
        text = msg.get("content") or ""
        if isinstance(text, list):
            text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
        usage = data.get("usage") or {}
        served = data.get("model", self.model)
        print(f"  [{served}] {usage.get('prompt_tokens', '?')} in / {usage.get('completion_tokens', '?')} out tokens"
              + ("" if served == self.model else f"  (fallback: {self.model} was unavailable)"))
        if not text.strip():
            raise ModelError("the model returned an empty reply")
        return text


def data_url(b64):
    mime = "image/png" if b64.startswith("iVBOR") else "image/jpeg"
    return f"data:{mime};base64,{b64}"


def error_text(e):
    try:
        body = json.loads(e.read().decode("utf-8", "replace"))
        err = body.get("error", body)
        msg = err.get("message", "") if isinstance(err, dict) else str(err)
        raw = (err.get("metadata") or {}).get("raw", "") if isinstance(err, dict) else ""
        return f"{msg} {raw}".strip()
    except Exception:
        return str(e)


def retry_after(e):
    try:
        return float(e.headers.get("Retry-After", ""))
    except (TypeError, ValueError):
        return None


def make_backend(props):
    backend = props.get("backend", "ollama").strip().lower()
    if backend == "ollama":
        selected = props.get("ollama.model", "qwen").strip()
        model = props.get(f"ollama.models.{selected}", OLLAMA_MODELS.get(selected, selected))
        if not model:
            raise SystemExit("ollama.model must be qwen, llama, or an installed vision model tag")
        options = dict(props)
        if model.split(":", 1)[0] == "llama3.2-vision":
            options["ollama.num_ctx"] = props.get("ollama.llama.num_ctx", "4096")
            options["ollama.num_batch"] = props.get("ollama.llama.num_batch", "32")
        return OllamaBackend(props.get("ollama.url", "http://localhost:11434/api/chat"),
                             model, timeout=positive_int(props, "ollama.timeout", 300),
                             num_ctx=positive_int(options, "ollama.num_ctx", 8192),
                             keep_alive=props.get("ollama.keep_alive", "5m"),
                             num_batch=positive_int(options, "ollama.num_batch", 128),
                             combine=props.get("ollama.images", "separate").strip().lower() == "combined")
    if backend == "openrouter":
        return OpenRouterBackend(
            props.get("openrouter.url", "https://openrouter.ai/api/v1/chat/completions"),
            props.get("openrouter.model", "google/gemma-4-31b-it:free"),
            openrouter_key(props),
            fallbacks=[m.strip() for m in props.get("openrouter.fallback_models", "").split(",")],
            app_name=props.get("openrouter.app_name", ""),
            retries=int(props.get("openrouter.retries", "3")))
    raise SystemExit(f"vlm.properties: backend must be 'ollama' or 'openrouter', not '{backend}'")


def positive_int(props, key, default):
    try:
        value = int(props.get(key, default))
        if value > 0:
            return value
    except (ValueError, TypeError):
        pass
    raise SystemExit(f"{key} must be a positive integer")


# ---------------------------------------------------------------- validation

def norm(name):
    return re.sub(r"[\s\-]+", "_", str(name).strip().lower())


def clean_step(step, known, waiting=False):
    """One raw step -> (clean action dict, None) or (None, error). With waiting=True a target that hasn't been
    seen yet is kept as written: such a step is never sent to the robot, it waits until the object is known."""
    if not isinstance(step, dict):
        return None, f"malformed step {step!r}"
    name = norm(step.get("action", ""))
    if name not in ACTIONS:
        return None, f"unknown action '{name}'; allowed: {', '.join(ACTIONS)}"
    clean = {"action": name, "target": "", "degrees": 0.0, "seconds": 0.0}
    param = ACTIONS[name]
    target = step.get("target")
    if param == "target" or (param is None and target):
        t = norm(target or "")
        if not t:
            return None, f"{name} needs a target"
        if t not in known and not waiting:
            return None, (f"'{t}' is not a known object; use an id from KNOWN OBJECTS, "
                          f"or search for it first (look_around, explore)")
        clean["target"] = known.get(t, t)
    elif param:
        try:
            value = float(step.get(param, 0))
        except (TypeError, ValueError):
            return None, f"bad {param} in {name}"
        clean[param] = max(-360.0, min(360.0, value)) if param == "degrees" else max(0.0, min(10.0, value))
    return clean, None


def clean_goal(raw, known):
    """The model's goal -> a list of conditions the bridge can check: {"kind": holding|in|open|closed, "object", "place"}.
    Ids of things not seen yet are kept as written; such a condition counts as "not found yet"."""
    goal = []
    for c in raw if isinstance(raw, list) else []:
        if not isinstance(c, dict):
            continue
        place = next((c[k] for k in ("in", "on", "at", "inside", "place") if isinstance(c.get(k), str) and c[k].strip()), None)
        if isinstance(c.get("holding"), str):
            cond = {"kind": "holding", "object": c["holding"], "place": ""}
        elif isinstance(c.get("object"), str) and place:
            cond = {"kind": "in", "object": c["object"], "place": place}
        elif isinstance(c.get("open"), str):
            cond = {"kind": "open", "object": c["open"], "place": ""}
        elif isinstance(c.get("closed"), str):
            cond = {"kind": "closed", "object": c["closed"], "place": ""}
        elif isinstance(c.get("robot_at"), str):
            cond = {"kind": "robot_at", "object": c["robot_at"], "place": ""}
        else:
            continue
        for k in ("object", "place"):
            cond[k] = known.get(norm(cond[k]), norm(cond[k])) if cond[k] else ""
        if cond["object"] and cond not in goal:
            goal.append(cond)
    return goal


def extract_json(raw):
    """The JSON object in a reply, also when it is wrapped in ```json fences or a sentence."""
    if not isinstance(raw, str):
        return None
    candidates = [raw] + re.findall(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if "{" in raw and "}" in raw:
        candidates.append(raw[raw.index("{"):raw.rindex("}") + 1])
    for text in candidates:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            continue
    return None


def parse_reply(raw, known_ids):
    """Raw VLM text -> {status, reply, question, goal, plan, error}. The first step must be usable right now
    (allowed action, known target), otherwise the reply is an error. Later steps may name a thing that hasn't
    been seen yet: they are kept, but they only run once that exact id is known. The plan ends at the first
    malformed step."""
    known = {norm(k): k for k in known_ids}
    out = {"status": "working", "reply": "", "question": "", "goal": [], "plan": [], "error": "", "things": []}
    data = extract_json(raw)
    if data is None:
        out["error"] = "reply was not valid JSON"
        return out
    if isinstance(data, list):
        data = {"plan": data}
    if not isinstance(data, dict):
        out["error"] = "reply was not a JSON object"
        return out

    steps = data.get("plan", data.get("actions")) or []
    if not isinstance(steps, list):
        steps = [steps]
    status = norm(data.get("status", "")) or ("working" if steps else "done")
    if status not in ("working", "done", "ask", "impossible"):
        status = "working" if steps else "done"
    things = [[known.get(norm(i), norm(i)) for i in th.get("ids") or [] if isinstance(i, str) and norm(i) in known]
              for th in data.get("things") or [] if isinstance(th, dict) and isinstance(th.get("ids") or [], list)] \
        if isinstance(data.get("things"), list) else []
    out["things"] = [ids for ids in things if ids]
    out.update(status=status, reply=str(data.get("reply", "")).strip(),
               question=str(data.get("question", "")).strip(), goal=clean_goal(data.get("goal"), known))

    if status in ("ask", "done", "impossible"):
        if status == "ask" and not (out["question"] or out["reply"]):
            out["error"] = "status was ask but there was no question"
        return out

    for i, step in enumerate(steps):
        clean, err = clean_step(step, known, waiting=i > 0)
        if err:
            if i == 0:
                out["error"] = err
            break
        out["plan"].append(clean)
    if not out["plan"] and not out["error"]:
        out["status"] = "done"
    return out


# ---------------------------------------------------------------- the world as data

SEARCH = ("explore", "look_around")
NEAR = 2.5     # metres: "the robot is at X"


class World:
    """What the robot knows, as data: the "facts" Unity sends for every known object. Everything the bridge decides
    for itself (is the goal reached, is a room searched, will a step fail) is read from here, never from the
    wording of the command."""

    def __init__(self, obs):
        self.known = {norm(k): k for k in obs.get("known") or []}
        self.facts = {norm(f["id"]): f for f in obs.get("facts") or [] if isinstance(f, dict) and f.get("id")}
        self.holding = norm(obs.get("holding") or "")

    def get(self, thing, field):
        return (self.facts.get(norm(thing)) or {}).get(field)

    def enclosure(self, thing):
        """The locker or cabinet whose door is in front of this place (itself, or the cabinet a shelf belongs to)."""
        if not thing:
            return ""
        if self.get(thing, "openable") and self.get(thing, "kind") != "door":
            return norm(thing)
        parent = norm(self.get(thing, "part_of") or "")
        return parent if parent and self.get(parent, "openable") else ""

    def where(self, thing):
        thing = norm(thing)
        if thing == self.holding:
            return "in the robot's hand"
        inside = self.get(thing, "inside")
        if inside:
            return f"{'in' if self.enclosure(inside) else 'on'} {inside}"
        return f"in {self.get(thing, 'room')}" if self.get(thing, "room") else "somewhere"

    def is_in(self, thing, place):
        thing, place = norm(thing), norm(place)
        if thing == self.holding:
            return False
        inside = norm(self.get(thing, "inside") or "")
        places = {inside, norm(self.get(inside, "part_of") or ""), norm(self.get(thing, "room") or ""), norm(self.get(thing, "area") or "")}
        return place in places - {""}

    def check(self, cond):
        """(True | False | None, sentence) for one goal condition. None = it names something not seen yet."""
        thing, place = cond["object"], cond["place"]
        missing = [x for x in (thing, place) if x and norm(x) not in self.known]
        if missing:
            return None, f"{missing[0]} has not been seen yet"
        if cond["kind"] == "holding":
            ok = norm(thing) == self.holding
            return ok, f"the robot is holding {thing}" if ok else f"the robot is not holding {thing} (it is {self.where(thing)})"
        if cond["kind"] == "in":
            ok = self.is_in(thing, place)
            return ok, f"{thing} is {self.where(thing)}" + ("" if ok else f", not in/on {place}")
        if cond["kind"] == "robot_at":
            ok = (self.get(thing, "distance") or 0) <= NEAR
            return ok, f"the robot is {'at' if ok else 'not at'} {thing}"
        want = cond["kind"] == "open"
        if not self.get(thing, "openable"):
            return False, f"{thing} can't be opened or closed"
        is_open = bool(self.get(thing, "open"))
        return is_open == want, f"{thing} is {'open' if is_open else 'closed'}"

    def is_place(self, thing):
        return self.get(thing, "kind") in ("room", "area")

    def left_to_search(self, place):
        """What an "explore <room or floor area>" still has to do there: enter the room, then every piece of
        furniture in it that can be looked into without a key. Empty when the place has been searched."""
        place = norm(place)
        field = "area" if self.get(place, "kind") == "area" else "room"
        todo = [] if field == "area" or self.get(place, "inspected") else ["enter"]
        return todo + sorted(k for k, f in self.facts.items()
                             if norm(f.get(field) or "") == place and f.get("holder") and not f.get("part_of")
                             and not f.get("inspected") and not (f.get("locked") and not f.get("open")))

    def hidden(self):
        """Places nobody has looked into and that can't be seen into from outside: rooms never entered and closed
        lockers or cupboards (a locked one only when its key has been seen). While one is left, something that
        hasn't been found may still be there."""
        out = []
        for k, f in self.facts.items():
            if f.get("kind") == "room" and not f.get("inspected"):
                out.append(f["id"])
            elif (f.get("holder") and f.get("openable") and not f.get("open") and not f.get("see_through")
                  and not f.get("inspected") and (not f.get("locked") or norm(f.get("key") or "") in self.known)):
                out.append(f["id"])
        return sorted(out)

    def search_status(self):
        """One sentence on where an unfound thing could still be."""
        hidden = self.hidden()
        if not hidden:
            return "Every room has been entered and every locker or cupboard that can be opened has been looked into"
        return "Not looked into yet: " + ", ".join(
            f"{h} (locked; its key {self.get(h, 'key')} is {self.where(self.get(h, 'key'))})" if self.get(h, "locked") else h
            for h in hidden)

    def unseen(self, goal, plan=()):
        """Ids the goal or the plan name that are not known objects (yet)."""
        ids = [x for c in goal for x in (c["object"], c["place"])] + [s["target"] for s in plan]
        return [x for i, x in enumerate(ids) if x and norm(x) not in self.known and x not in ids[:i]]

    def foresee(self, plan):
        """The first step of a plan that is certain to fail as things stand, as a sentence; else None. It follows
        the hand and the doors through the plan, and stops where the outcome can't be known in advance."""
        if not self.facts:
            return None
        hand, doors = self.holding, {}
        is_open = lambda x: doors.get(x, bool(self.get(x, "open")))
        for step in plan:
            action, x = step["action"], norm(step.get("target") or "")
            if x and x not in self.known:
                return None
            if action == "explore" and self.get(x, "locked") and not is_open(x) and hand != norm(self.get(x, "key") or ""):
                action = "open"       # looking into a locked locker means opening it
            elif action in SEARCH:
                return None
            if action == "pick":
                if hand:
                    lock = next((k for k, f in self.facts.items() if f.get("locked") and norm(f.get("key") or "") == hand and k not in doors), "")
                    return (f"'pick {x}' would fail: the hand already holds {hand}, and it holds one thing at a time. "
                            + (f"Use the key first: open {lock} with it (the key stays in the lock and the hand is free again), "
                               f"and pick up {x} after that" if lock else f"Put {hand} down on a surface nearby first"))
                if not self.get(x, "pickable"):
                    return f"'pick {x}' would fail: {x} can't be picked up"
                box = self.enclosure(self.get(x, "inside"))
                if box and not is_open(box):
                    return f"'pick {x}' would fail: {box} is closed. Open it first"
                hand = x
            elif action == "place":
                if not hand:
                    return f"'place {x}' would fail: the hand is empty. Pick up the object first"
                if not self.get(x, "holder"):
                    return f"'place {x}' would fail: nothing can be put on or in {x}"
                box = self.enclosure(x)
                if box and not is_open(box):
                    return f"'place {x}' would fail: {box} is closed. Open it first"
                hand = ""
            elif action == "open":
                if not self.get(x, "openable"):
                    return f"'open {x}' would fail: {x} can't be opened"
                if self.get(x, "locked") and x not in doors:
                    key = norm(self.get(x, "key") or "")
                    if hand != key:
                        return (f"'open {x}' would fail: {x} is locked and opens only while the robot holds {key}"
                                + (f" (it would be holding {hand})" if hand else "")
                                + (f". {key} is {self.where(key)}. Do it in this order: pick {key}, open {x} (the key stays "
                                   f"in the lock and the hand is free again), and only then pick up anything else"
                                   + (f"; so put {hand} down on a surface nearby first" if hand else "")
                                   if key in self.known else f". {key} has not been seen yet"))
                    hand = ""     # the key stays in the lock
                doors[x] = True
            elif action == "close":
                if not self.get(x, "openable"):
                    return f"'close {x}' would fail: {x} can't be closed"
                doors[x] = False
        return None


def show(action):
    if action["action"] == "rotate":
        return f"rotate {action['degrees']:g} deg"
    if action["action"] == "wait":
        return f"wait {action['seconds']:g} s"
    return f"{action['action']} {action['target']}".strip()


def show_goal(cond):
    if cond["kind"] == "in":
        return f"{cond['object']} in/on {cond['place']}"
    return f"{cond['kind'].replace('_', ' ')} {cond['object']}"


# ---------------------------------------------------------------- the loop

class Task:
    def __init__(self, command):
        self.command = command
        self.answers = []        # (question, answer)
        self.history = []        # one line per action or planner problem
        self.steps = 0
        self.failures = 0
        self.question = None     # waiting for the user's answer
        self.goal = []           # what must be true at the end, as the model stated it (see clean_goal)
        self.plan = []           # remaining planned steps (used in after_plan mode)
        self.searching = False   # the plan is waiting for something not seen yet; its search steps end when it shows up
        self.search_left = None  # what was left to search in the room before the explore step that is running
        self.fresh = []          # objects first seen since the model was last asked
        self.streak = 0          # failed actions in a row
        self.blocked = {}        # step -> error, for steps that failed since the last success or user answer
        self.asked = set()       # missing keys the user has already been asked about
        self.just_done = None    # the step that succeeded last turn, to catch a model that repeats it for no reason
        self.chosen = False      # the model has been asked once whether it chose between look-alikes by itself
        self.unconfirmed = 0     # times the model said "done" while the world said otherwise
        self.current = None      # the action Unity is running


class Brain:
    """Turns Unity observations into the next message for Unity. One per connection."""

    def __init__(self, vlm, feedback="every_step", log=print):
        self.vlm, self.feedback, self.log = vlm, feedback, log
        self.task = None

    def handle(self, obs):
        kind = obs.get("type", "command")
        if kind == "cancel":
            self.task = None
            return self.msg("done", reply="Stopped.")
        if kind in ("command", "answer"):
            text = str(obs.get("text", "")).strip()
            if kind == "answer" and self.task and self.task.question:
                self.task.answers.append((self.task.question, text))
                self.task.question = None
                self.task.streak = 0          # the user's answer is new information: earlier dead ends may work now
                self.task.blocked.clear()
            else:
                self.task = Task(text)
            return self.decide(obs)
        if kind == "result":
            t = self.task
            if not t or not t.current:
                return self.msg("done", error="got a result but no task is running")
            t.steps += 1
            line = show(t.current)
            error = ""
            if obs.get("ok", True):
                line += " -> ok"
                if obs.get("note"):
                    line += f" ({obs['note']})"
                t.streak = 0
                t.blocked.clear()
                t.just_done = show(t.current)
            else:
                t.just_done = None
                error = str(obs.get("error") or "unknown error")
                line += f" -> FAILED: {error}"
                t.failures += 1
                t.streak += 1
                t.blocked[show(t.current)] = error
                t.plan = []
            if obs.get("newly_seen"):
                line += f"; newly seen: {', '.join(obs['newly_seen'])}"
                t.fresh += [n for n in obs["newly_seen"] if n not in t.fresh]
            t.history.append(line)
            failed, t.current = t.current, None
            return self.decide(obs, last_ok=not error, last_error=error, failed=failed if error else None)
        return self.msg("done", error=f"unknown message type '{kind}'")

    def blocker(self, t, world, error, failed):
        """A question for the user when the robot is blocked and shouldn't keep trying on its own, else None.
        It may fix an obvious problem itself, but a missing key or repeated dead ends go straight to the user."""
        target = failed.get("target") if failed else ""
        key = world.get(target, "key") if failed and failed["action"] == "open" and world.get(target, "locked") else ""
        if key and norm(key) not in world.known and key not in t.asked:
            t.asked.add(key)
            return (f"{target} is locked and I haven't seen {key} yet. "
                    f"Do you know where it is, or should I search for it?")
        if t.streak >= STUCK_AFTER:
            return f"I'm stuck after {t.streak} tries. The last problem: {error}. What should I do?"
        return None

    def ask(self, question, raw=""):
        self.task.question = question
        return self.msg("ask", reply=question, raw=raw)

    def planned(self, t, world, obs):
        """In after_plan mode: the next step of the plan the model already gave, when nothing calls for a new
        decision. Returns a step, "done" when the plan is finished and the goal is verifiably reached, or None
        when the model has to look again."""
        waiting = world.unseen(t.goal, t.plan)
        while t.plan:
            step = t.plan[0]
            if step["target"] and norm(step["target"]) not in world.known:
                self.note(t, f"the plan stopped at '{show(step)}': no object with the id {step['target']} has been seen. "
                             f"If an object in KNOWN OBJECTS is what the user means, use its exact id (and ask if more "
                             f"than one fits). Otherwise keep searching. {world.search_status()}")
                t.plan = []
                return None
            if step["action"] in SEARCH:
                if t.searching and not waiting:
                    t.plan.pop(0)             # everything the plan was looking for has been seen: the search is over
                    continue
                room = step["target"] if world.is_place(step["target"]) else ""
                if room:
                    left = world.left_to_search(room)
                    stalled = t.just_done == show(step) and left == t.search_left and not obs.get("newly_seen")
                    if not left or stalled:
                        t.plan.pop(0)         # the room has been searched (or one more step would change nothing)
                        continue
                    t.search_left = left
                    return dict(step)         # "explore <room>" is repeated until the whole room has been searched
                return t.plan.pop(0)
            problem = world.foresee(t.plan)
            if problem:
                self.note(t, f"the plan was stopped before it failed: {problem}. Give a new plan that fixes this")
                t.plan = []
                return None
            return clean_step(t.plan.pop(0), world.known)[0]
        if t.goal and world.facts and all(world.check(c)[0] for c in t.goal):
            return "done"
        return None

    def note(self, t, text):
        t.history.append(f"({text})")
        self.log(text)

    def decide(self, obs, last_ok=False, last_error="", failed=None):
        t = self.task
        world = World(obs)
        known = obs.get("known") or []
        rejected, told = 0, ""
        unsure = "I'm not sure what to do next. Can you tell me the next step, or say it another way?"
        while True:
            if t.steps >= MAX_STEPS:
                return self.finish("fail", f"I used all {MAX_STEPS} actions without finishing, so I'm stopping here.")
            if t.failures > MAX_FAILURES:
                return self.finish("fail", f"I ran into {t.failures} problems and can't find a way to "
                                           f"finish this, so I'm stopping.")

            question = last_error and self.blocker(t, world, last_error, failed)
            last_error = ""
            if question:
                return self.ask(question)

            if self.feedback == "after_plan" and last_ok:
                step = self.planned(t, world, obs)
                if step == "done":            # the state of the world proves it; no need to ask the model
                    return self.finish("done", "Done: " + "; ".join(world.check(c)[1] for c in t.goal) + ".")
                if step:
                    return self.act(step, reply="", raw="")
            last_ok = False

            prompt = build_prompt(t, obs, world)
            images = [b for b in (obs.get("image_b64"), obs.get("overhead_b64")) if b]
            try:
                raw = self.vlm(SYSTEM_PROMPT, prompt, images)
            except Exception as e:  # model server down, out of free requests, timeout...
                return self.finish("fail", "The vision model is unavailable right now.", error=str(e))

            r = parse_reply(raw, known)
            if r["error"]:
                t.failures += 1
                self.note(t, f"your last reply was rejected: {r['error']}")
                self.log(f"  raw: {raw}")
                rejected += 1
                if rejected >= 2:   # don't keep the user waiting while the model flounders
                    return self.ask(unsure, raw=raw)
                continue
            if r["goal"] or not t.goal:
                t.goal = r["goal"]
            t.fresh = []

            if r["status"] == "ask":
                question = r["question"] or r["reply"]
                answered = next((ans for q, ans in t.answers if norm(q) == norm(question)), None)
                if answered is not None:   # asking again what the user already answered would loop forever
                    self.note(t, f'you already asked "{question}" and the user answered "{answered}". '
                                 f"Don't ask it again: act on that answer now")
                    rejected += 1
                    if rejected >= 2:
                        return self.ask(unsure, raw=raw)
                    continue
                return self.ask(question, raw=raw)
            if r["status"] == "done":
                # The model says done; the world has the last word. Never report a success that isn't one.
                wrong = [text for ok, text in map(world.check, t.goal) if not ok] if world.facts else []
                if wrong:
                    t.unconfirmed += 1
                    if t.unconfirmed > 2:
                        return self.finish("fail", f"I couldn't finish this: {'; '.join(wrong)}.", raw=raw)
                    self.note(t, f"you said done, but your own goal is NOT reached: {'; '.join(wrong)}. Continue with the "
                                 f"steps that fix this, or return status impossible if it can't be done")
                    continue
            if r["status"] == "impossible" and world.unseen(t.goal) and world.hidden() and t.unconfirmed < 2:
                # Giving up on a thing that hasn't been found, while rooms or lockers have never been looked into.
                t.unconfirmed += 1
                self.note(t, f"you can't call this impossible yet: {', '.join(world.unseen(t.goal))} has not been found. "
                             f"{world.search_status()}. Look there before giving up")
                continue
            if r["status"] in ("done", "impossible"):
                default = "Done." if r["status"] == "done" else "I can't do that."
                return self.finish("done" if r["status"] == "done" else "fail", r["reply"] or default, raw=raw)

            first = r["plan"][0]
            used = {norm(c["object"]) for c in r["goal"]} | {norm(x["target"]) for x in r["plan"]}
            choice = next((ids for ids in r["things"] if len(ids) > 1 and len(used & set(map(norm, ids))) == 1), None)
            held = {c["object"] for c in r["goal"] if c["kind"] == "holding"} & {c["object"] for c in r["goal"] if c["kind"] == "in"}
            if choice and not t.answers and not t.chosen:
                # By its own list several known objects fit the user's words, and it went ahead with one of them.
                t.chosen = True
                self.note(t, f"you listed {', '.join(choice)} as fitting the same words of the user, and then chose one. If "
                             f"the user's words really single out one of them, list only that id under things. If they fit "
                             f"equally well, return status ask and let the user choose")
                continue
            if held and rejected == 0:
                self.note(t, f"your goal contradicts itself: {', '.join(held)} can't be in the robot's hand AND put down "
                             f"somewhere at the end. Keep only the entry that says where it must END UP")
                rejected += 1
                continue
            if first["action"] in ("move_to", "open", "close") and show(first) == t.just_done:
                # The step just succeeded and doing it again changes nothing: a model stuck on a no-op.
                self.note(t, f"you proposed '{show(first)}' again, but it already succeeded and repeating it "
                             f"changes nothing. Give the NEXT step of the task, or status done if it is finished")
                rejected += 1
                if rejected >= 2:
                    return self.ask(unsure, raw=raw)
                continue
            repeat = t.blocked.get(show(first))
            if repeat:   # the same step that just failed, with nothing changed since: ask instead of looping
                t.failures += 1
                return self.ask(f"I'm stuck: {repeat}. What should I do?", raw=raw)
            problem = world.foresee(r["plan"])
            missing = world.unseen(t.goal, r["plan"])
            searched = first["action"] == "explore" and world.is_place(first["target"]) and not world.left_to_search(first["target"])
            if searched:
                problem = (f"{first['target']} has already been searched completely; exploring it again finds nothing. "
                           f"{world.search_status()}")
            early = next((x for x in r["plan"] if x["action"] in SEARCH or x["action"] == "pick"), None)
            if missing and early and early["action"] == "pick" and norm(early["target"]) in world.known and rejected == 0:
                # Carrying something around while searching blocks the hand (a key may be needed on the way).
                problem = (f"{', '.join(missing)} has not been seen yet. Find it FIRST, with an empty hand "
                           f"(look_around, explore), and pick up {early['target']} only after that")
            if problem and (problem != told or searched):
                # A step that is certain to fail (hand full, locker locked...): let the model fix the plan now,
                # while nothing has moved, instead of walking into the failure.
                self.note(t, f"your plan was not started: {problem}. Give a plan that fixes this")
                told = problem
                rejected += 1
                if rejected > 2:
                    return self.ask(unsure, raw=raw)
                continue

            t.plan = r["plan"][1:]
            t.searching = bool(missing)
            t.search_left = world.left_to_search(first["target"]) if first["action"] == "explore" and first["target"] else None
            if first["action"] == "explore" and world.is_place(first["target"]):
                t.plan.insert(0, dict(first))   # a room is searched to the end (see planned)
            return self.act(first, reply=r["reply"], raw=raw)

    def act(self, step, reply, raw):
        self.task.current = step
        return self.msg("act", reply=reply, raw=raw, next=step)

    def finish(self, kind, reply, raw="", error=""):
        m = self.msg(kind, reply=reply, raw=raw, error=error)
        self.task = None
        return m

    def msg(self, kind, reply="", raw="", error="", next=None):
        t = self.task
        m = {"kind": kind, "reply": reply, "raw": raw, "error": error,
             "step": t.steps if t else 0, "maxSteps": MAX_STEPS,
             "failures": t.failures if t else 0, "maxFailures": MAX_FAILURES}
        if next:
            m["next"] = next
        return m


def build_prompt(t, obs, world=None):
    world = world or World(obs)
    lines = [f"TASK: {t.command}"]
    if t.answers:
        lines.append("YOUR QUESTIONS AND THE USER'S ANSWERS:")
        lines += [f"  you asked: {q} / user answered: {a}" for q, a in t.answers]
    imgs = []
    if obs.get("image_b64"):
        imgs.append("robot eye camera")
    if obs.get("overhead_b64"):
        imgs.append("overhead camera")
    lines.append("IMAGES: " + (", ".join(f"{i + 1} = {name}" for i, name in enumerate(imgs)) or "none this turn"))
    lines.append(f"ROBOT: {obs.get('location') or 'unknown position'}; holding: {obs.get('holding') or 'nothing'}")
    lines.append("KNOWN OBJECTS:")
    lines += [f"  - {s}" for s in (obs.get("states") or obs.get("known") or [])]
    if obs.get("unexplored"):
        lines.append("NOT EXPLORED YET:")
        lines += [f"  - {u}" for u in obs["unexplored"]]
    if t.fresh:
        lines.append(f"FIRST SEEN SINCE YOUR LAST REPLY: {', '.join(t.fresh)}")
    lines.append(f"ACTIONS SO FAR ({t.steps} of {MAX_STEPS} used, {t.failures} of {MAX_FAILURES} problems):")
    lines += [f"  {i + 1}. {h}" for i, h in enumerate(t.history)] or ["  none yet"]
    if t.goal and world.facts:
        lines.append("GOAL CHECK (the goal you gave, checked against the world right now):")
        for c in t.goal:
            ok, text = world.check(c)
            lines.append(f"  - {show_goal(c)}: {'REACHED' if ok else 'NOT FOUND YET' if ok is None else 'NOT YET'} ({text})")
    lines.append("Reply with the JSON.")
    return "\n".join(lines)


# ---------------------------------------------------------------- server

async def handler(ws):
    print("Unity connected")
    brain = Brain(BACKEND, feedback=FEEDBACK)
    async for message in ws:
        try:
            obs = json.loads(message)
        except json.JSONDecodeError:
            continue
        if obs.get("type") in ("command", "answer"):
            print(f"\n> {obs.get('text', '')}")
        elif obs.get("type") == "result":
            print("  result:", "ok" if obs.get("ok") else f"FAILED: {obs.get('error')}",
                  f"({obs['note']})" if obs.get("note") else "")
        result = await asyncio.to_thread(brain.handle, obs)
        if result["raw"]:
            print("  raw VLM:", result["raw"])
        if result["error"]:
            print("  error:", result["error"])
        print(f"  -> {result['kind']}", show(result["next"]) if result.get("next") else result["reply"])
        await ws.send(json.dumps(result))


async def serve():
    import websockets  # imported here so --test and --selftest work without it
    async with websockets.serve(handler, HOST, PORT, max_size=20_000_000):
        print(f"VLM bridge on ws://{HOST}:{PORT} using {BACKEND.name} (feedback: {FEEDBACK})")
        await asyncio.Future()


# ---------------------------------------------------------------- offline checks

def selftest_backends():
    """Verify backend selection and the HTTP/image contract without contacting any model."""
    from unittest.mock import patch
    from PIL import Image

    # Local selection must never read an OpenRouter key, including on failure.
    with patch(__name__ + ".openrouter_key", side_effect=AssertionError("unexpected cloud credentials")):
        assert make_backend({}).model == OLLAMA_MODELS["qwen"]
        assert make_backend({"ollama.model": "llama"}).model == OLLAMA_MODELS["llama"]
        llama = make_backend({"ollama.model": "llama", "ollama.llama.num_ctx": "2048",
                              "ollama.llama.num_batch": "16"})
        assert llama.num_ctx == 2048 and llama.num_batch == 16
        assert make_backend({"ollama.model": "qwen3-vl:4b-instruct"}).model == "qwen3-vl:4b-instruct"
        assert make_backend({"ollama.model": "qwen", "ollama.models.qwen": "qwen3-vl:4b-instruct"}).model == "qwen3-vl:4b-instruct"
        for key in ("ollama.timeout", "ollama.num_ctx", "ollama.num_batch"):
            for invalid in ("0", "-1", "not-a-number"):
                try:
                    make_backend({key: invalid})
                except SystemExit as e:
                    assert key in str(e)
                else:
                    raise AssertionError(f"accepted invalid {key}")

    with patch(__name__ + ".openrouter_key", return_value="test-only-placeholder") as key:
        assert isinstance(make_backend({"backend": "openrouter"}), OpenRouterBackend)
        key.assert_called_once()

    def encoded(color):
        output = io.BytesIO()
        Image.new("RGB", (80, 60), color).save(output, format="PNG")
        return base64.b64encode(output.getvalue()).decode()

    images = [encoded("red"), encoded("blue")]
    reply = '{"status":"done","reply":"Done","plan":[]}'
    for selected in ("qwen", "llama"):
        backend = make_backend({"ollama.model": selected, "ollama.timeout": "42", "ollama.num_ctx": "4096"})
        for camera_images in ([], images[:1], images):
            response = io.BytesIO(json.dumps({"message": {"content": reply}}).encode())
            with patch("urllib.request.urlopen", return_value=response) as post:
                assert backend("system", "task", camera_images) == reply
            req = post.call_args.args[0]
            body = json.loads(req.data)
            assert post.call_args.kwargs["timeout"] == 42
            assert body["model"] == OLLAMA_MODELS[selected]
            assert body["format"] == "json" and body["stream"] is False
            assert body["options"]["num_ctx"] == 4096
            assert body["options"]["num_batch"] == (32 if selected == "llama" else 128)
            sent = body["messages"][-1].get("images", [])
            if selected == "llama" and len(camera_images) == 2:
                assert len(sent) == 1 and "LEFT panel" in body["messages"][0]["content"]
                with Image.open(io.BytesIO(base64.b64decode(sent[0]))) as merged:
                    assert merged.getpixel((40, 58)) == (255, 0, 0)
                    assert merged.getpixel((132, 58)) == (0, 0, 255)
            else:
                assert sent == camera_images

    for invalid in (["broken", images[1]], [base64.b64encode(b"not an image").decode(), images[1]]):
        try:
            combine_camera_images(invalid)
        except ModelError:
            pass
        else:
            raise AssertionError("invalid image accepted")

    backend = make_backend({})
    errors = [
        (urllib.error.HTTPError(backend.url, 404, "missing", {}, io.BytesIO(b'{"error":"model not found"}')), "ollama.sh pull"),
        (urllib.error.HTTPError(backend.url, 500, "error", {}, io.BytesIO(b'{"error":"out of memory"}')), "out of memory"),
        (urllib.error.URLError("connection refused"), "tools/run_local.py"),
        (TimeoutError(), "ollama.timeout"),
    ]
    with patch(__name__ + ".openrouter_key", side_effect=AssertionError("unexpected cloud fallback")):
        for error, expected in errors:
            with patch("urllib.request.urlopen", side_effect=error):
                try:
                    backend("system", "task", [])
                except ModelError as e:
                    assert expected in str(e), str(e)
                else:
                    raise AssertionError("backend error not reported")
        for invalid in (b"not-json", b"[]", b'{"message":{}}', b'{"message":{"content":""}}'):
            with patch("urllib.request.urlopen", return_value=io.BytesIO(invalid)):
                try:
                    backend("system", "task", [])
                except ModelError:
                    pass
                else:
                    raise AssertionError("invalid model response accepted")


def selftest():
    """Drives the loop with a scripted fake model, so the logic can be checked without Ollama or Unity."""
    selftest_backends()
    def scripted(*replies):
        it = iter(replies)
        return lambda system, prompt, images: json.dumps(next(it))

    obs = {"known": ["basketball", "ball_rack", "main_hall"], "states": [], "image_b64": "", "overhead_b64": ""}
    quiet = lambda *a: None

    b = Brain(scripted(
        {"status": "working", "reply": "On it.", "plan": [{"action": "move_to", "target": "basketball"},
                                                          {"action": "pick", "target": "basketball"}]},
        {"status": "working", "plan": [{"action": "pick", "target": "Basketball"}]},
        {"status": "done", "reply": "Got it.", "plan": []}), log=quiet)
    m = b.handle({**obs, "type": "command", "text": "pick up the basketball"})
    assert m["kind"] == "act" and m["next"]["target"] == "basketball", m
    m = b.handle({**obs, "type": "result", "ok": True})
    assert m["kind"] == "act" and m["next"]["action"] == "pick" and m["step"] == 1, m
    m = b.handle({**obs, "type": "result", "ok": True, "holding": "basketball"})
    assert m["kind"] == "done" and m["reply"] == "Got it.", m

    # unknown target -> rejected, counted, and the model is asked again with the reason
    prompts = []
    def recorder(system, prompt, images):
        prompts.append(prompt)
        return json.dumps([{"status": "working", "plan": [{"action": "pick", "target": "gold_medal"}]},
                           {"status": "working", "plan": [{"action": "explore", "target": "main hall"}]}][len(prompts) - 1])
    b = Brain(recorder, log=quiet)
    m = b.handle({**obs, "type": "command", "text": "get the gold medal"})
    assert m["kind"] == "act" and m["next"] == {"action": "explore", "target": "main_hall", "degrees": 0.0, "seconds": 0.0}, m
    assert m["failures"] == 1 and "not a known object" in prompts[1], prompts

    # ask-back: the answer continues the same task
    b = Brain(scripted({"status": "ask", "question": "Small or large?", "plan": []},
                       {"status": "working", "plan": [{"action": "look_around"}]}), log=quiet)
    m = b.handle({**obs, "type": "command", "text": "get the red dumbbell"})
    assert m["kind"] == "ask" and m["reply"] == "Small or large?", m
    m = b.handle({**obs, "type": "answer", "text": "the small one"})
    assert m["kind"] == "act" and b.task.answers == [("Small or large?", "the small one")], m

    # stuck: proposing the step that just failed again makes the robot ask the user instead of looping
    calls = []
    def stubborn(system, prompt, images):
        calls.append(prompt)
        return json.dumps({"plan": [{"action": "open", "target": "ball_rack"}]})
    b = Brain(stubborn, log=quiet)
    m = b.handle({**obs, "type": "command", "text": "open the rack"})
    assert m["kind"] == "act", m
    m = b.handle({**obs, "type": "result", "ok": False, "error": "ball_rack can't be opened"})
    assert m["kind"] == "ask" and "ball_rack can't be opened" in m["reply"] and len(calls) == 2, m
    # ... the answer clears the dead end, so the step may be tried again; in the end the failure limit still applies
    while m["kind"] == "ask":
        m = b.handle({**obs, "type": "answer", "text": "try again"})
        if m["kind"] == "act":
            m = b.handle({**obs, "type": "result", "ok": False, "error": "ball_rack can't be opened"})
    assert m["kind"] == "fail" and m["failures"] == MAX_FAILURES + 1, m

    # stuck: a locked locker whose key hasn't been seen is asked about at once, without another model call
    calls.clear()
    def fact(id, **more):
        return {"id": id, "kind": "", "inside": "", "part_of": "", "room": "main_hall", "key": "", "pickable": False,
                "holder": False, "openable": False, "open": False, "locked": False, "inspected": True, **more}
    def seeing(*facts, **more):
        return {**obs, "known": [f["id"] for f in facts], "facts": list(facts), **more}
    locker_5 = fact("locker_5", kind="locker", holder=True, openable=True, locked=True, key="locker_key", inspected=False)
    lock = seeing(locker_5)
    b = Brain(lambda s, p, i: calls.append(p) or json.dumps({"plan": [{"action": "open", "target": "locker_5"}]}), log=quiet)
    m = b.handle({**lock, "type": "command", "text": "open locker 5"})
    m = b.handle({**lock, "type": "result", "ok": False, "error": "locker_5 is locked; it needs locker_key"})
    # (2 calls: the plan was first sent back because the lock makes it certain to fail; none after the failure)
    assert m["kind"] == "ask" and "locker_key" in m["reply"] and len(calls) == 2, (m, len(calls))
    assert "opens only while the robot holds locker_key" in calls[1], calls[1]
    # ... but when the key is already known the robot goes on by itself
    b = Brain(scripted({"plan": [{"action": "open", "target": "locker_5"}]},
                       {"plan": [{"action": "pick", "target": "locker_key"}]}), log=quiet)
    seen = seeing(locker_5, fact("locker_key", pickable=True))
    m = b.handle({**seen, "type": "command", "text": "open locker 5"})
    assert m["kind"] == "act" and m["next"]["target"] == "locker_key", m

    # stuck: several different steps failing in a row also end in a question
    plans = iter([{"plan": [{"action": a, "target": "ball_rack"}]} for a in ("open", "close", "pick", "place")])
    b = Brain(lambda s, p, i: json.dumps(next(plans)), log=quiet)
    m = b.handle({**obs, "type": "command", "text": "do something with the rack"})
    for n in range(STUCK_AFTER):
        assert m["kind"] == "act", (n, m)
        m = b.handle({**obs, "type": "result", "ok": False, "error": "that didn't work"})
    assert m["kind"] == "ask" and "that didn't work" in m["reply"], m

    # a question the user already answered is not asked again; the model is told to act on the answer
    seen_prompts = []
    replies = iter([{"status": "ask", "question": "Which locker room is locker 5 in?", "plan": []},
                    {"status": "ask", "question": "Which locker room is locker 5 in?", "plan": []},
                    {"status": "working", "plan": [{"action": "look_around"}]}])
    b = Brain(lambda s, p, i: seen_prompts.append(p) or json.dumps(next(replies)), log=quiet)
    m = b.handle({**obs, "type": "command", "text": "put the dumbbell in locker 5"})
    assert m["kind"] == "ask", m
    m = b.handle({**obs, "type": "answer", "text": "locker room b"})
    assert m["kind"] == "act" and "already asked" in seen_prompts[-1] and "locker room b" in seen_prompts[-1], m

    # a step that just succeeded is not run again: the model is told to move on (it was looping on a no-op)
    seen_prompts = []
    replies = iter([{"plan": [{"action": "move_to", "target": "ball_rack"}]}, {"plan": [{"action": "move_to", "target": "ball_rack"}]},
                    {"plan": [{"action": "place", "target": "ball_rack"}]}])
    b = Brain(lambda s, p, i: seen_prompts.append(p) or json.dumps(next(replies)), feedback="every_step", log=quiet)
    m = b.handle({**obs, "type": "command", "text": "put the basketball on the rack"})
    m = b.handle({**obs, "type": "result", "ok": True})
    assert m["kind"] == "act" and m["next"]["action"] == "place" and "already succeeded" in seen_prompts[-1], m

    # "done" is checked against the goal the model itself stated, using the world facts: no wording involved
    cabinet = fact("trophy_cabinet", kind="cabinet", openable=True, holder=True)
    shelf = fact("trophy_cabinet_top", kind="shelf", part_of="trophy_cabinet", holder=True)
    medal_goal = [{"object": "gold_medal", "in": "trophy_cabinet"}, {"closed": "trophy_cabinet"}]
    w = World(seeing(cabinet, shelf, fact("gold_trophy", pickable=True, inside="trophy_cabinet_top")))
    goal = clean_goal(medal_goal, w.known)
    assert [w.check(c)[0] for c in goal] == [None, True], goal          # the medal has not been seen: not reached
    w = World(seeing(cabinet, shelf, fact("gold_medal", pickable=True, inside="trophy_cabinet_top")))
    assert all(w.check(c)[0] for c in clean_goal(medal_goal, w.known))    # on a shelf of the cabinet = in the cabinet
    w = World(seeing({**cabinet, "open": True}, shelf, fact("gold_medal", pickable=True, inside="trophy_cabinet_top")))
    assert [w.check(c)[0] for c in clean_goal(medal_goal, w.known)] == [True, False]
    w = World(seeing(fact("soccer_ball", pickable=True), fact("main_hall", kind="room"), holding="soccer_ball"))
    assert w.check(clean_goal([{"holding": "Soccer Ball"}], w.known)[0])[0] is True
    assert w.check(clean_goal([{"object": "soccer_ball", "on": "main_hall"}], w.known)[0])[0] is False   # it is in the hand

    # the model says done although its own goal is not reached: it is told why, and has to carry on
    seen_prompts = []
    trophy_in = seeing(cabinet, shelf, fact("gold_trophy", pickable=True, inside="trophy_cabinet_top"),
                       fact("locker_room_b", kind="room", inspected=False))
    replies = iter([{"goal": medal_goal, "status": "done", "reply": "The gold medal is in the trophy cabinet.", "plan": []},
                    {"goal": medal_goal, "plan": [{"action": "explore", "target": "locker_room_b"}]}])
    b = Brain(lambda s, p, i: seen_prompts.append(p) or json.dumps(next(replies)), log=quiet)
    m = b.handle({**trophy_in, "type": "command", "text": "get the gold medal and put it in the trophy cabinet"})
    assert m["kind"] == "act" and m["next"]["action"] == "explore", m
    assert "NOT reached" in seen_prompts[-1] and "gold_medal has not been seen yet" in seen_prompts[-1], seen_prompts[-1]
    assert "NOT FOUND YET" in seen_prompts[-1]
    # ... and a model that keeps claiming success ends in an honest failure, not a false "done"
    b = Brain(lambda s, p, i: json.dumps({"goal": [{"object": "blue_dumbbell", "in": "locker_5"}], "status": "done"}), log=quiet)
    wrong = seeing(fact("blue_dumbbell", pickable=True, inside="locker_3"), fact("locker_3", holder=True, openable=True))
    m = b.handle({**wrong, "type": "command", "text": "put the blue dumbbell in locker 5"})
    assert m["kind"] == "fail" and "locker_5" in m["reply"], m

    # after_plan: when the plan is finished and the goal is verifiably reached, the robot is done without a model call
    calls.clear()
    ball, rack = fact("basketball", pickable=True), fact("ball_rack", holder=True)
    level = fact("ball_rack_top", part_of="ball_rack", holder=True)
    b = Brain(lambda s, p, i: calls.append(p) or json.dumps(
        {"goal": [{"object": "basketball", "in": "ball_rack"}],
         "plan": [{"action": "pick", "target": "basketball"}, {"action": "place", "target": "ball_rack"}]}),
        feedback="after_plan", log=quiet)
    m = b.handle({**seeing(ball, rack, level), "type": "command", "text": "the basketball belongs on the rack"})
    m = b.handle({**seeing(ball, rack, level, holding="basketball"), "type": "result", "ok": True})
    assert m["kind"] == "act" and m["next"]["action"] == "place", m
    m = b.handle({**seeing({**ball, "inside": "ball_rack_top"}, rack, level), "type": "result", "ok": True})
    assert m["kind"] == "done" and "basketball is on ball_rack_top" in m["reply"] and len(calls) == 1, (m, len(calls))

    # after_plan: a room is searched to the end, and steps that name an unseen thing wait until it has been seen
    calls.clear()
    room = fact("locker_room_a", kind="room", room="locker_room_a", inspected=False)
    l1 = fact("locker_1", kind="locker", room="locker_room_a", holder=True, openable=True, inspected=False)
    l2 = {**l1, "id": "locker_2"}
    key = fact("locker_key", pickable=True, inside="locker_1", room="locker_room_a")
    plan = {"goal": [{"holding": "locker_key"}], "plan": [{"action": "explore", "target": "locker_room_a"},
                                                         {"action": "pick", "target": "locker_key"}]}
    b = Brain(lambda s, p, i: calls.append(p) or json.dumps(plan), feedback="after_plan", log=quiet)
    m = b.handle({**seeing(room), "type": "command", "text": "I lost my key, find it"})
    assert m["next"] == {"action": "explore", "target": "locker_room_a", "degrees": 0.0, "seconds": 0.0}, m
    entered = {**room, "inspected": True}
    m = b.handle({**seeing(entered, l1, l2), "type": "result", "ok": True, "newly_seen": ["locker_1", "locker_2"]})
    assert m["kind"] == "act" and m["next"]["action"] == "explore", m                    # lockers left: keep searching
    opened = {**l1, "open": True, "inspected": True}
    m = b.handle({**seeing(entered, opened, l2, key), "type": "result", "ok": True, "newly_seen": ["locker_key"]})
    assert m["next"]["action"] == "pick" and m["next"]["target"] == "locker_key", m     # found: locker_2 is skipped
    m = b.handle({**seeing(entered, opened, l2, key, holding="locker_key"), "type": "result", "ok": True})
    assert m["kind"] == "done" and len(calls) == 1, (m, len(calls))
    # ... if the thing never shows up, the search covers the whole room and then the model decides again
    b = Brain(lambda s, p, i: calls.append(p) or json.dumps(plan), feedback="after_plan", log=quiet)
    calls.clear()
    m = b.handle({**seeing(room), "type": "command", "text": "I lost my key, find it"})
    m = b.handle({**seeing(entered, l1), "type": "result", "ok": True, "newly_seen": ["locker_1"]})
    assert m["next"]["action"] == "explore" and len(calls) == 1, m
    m = b.handle({**seeing(entered, {**l1, "open": True, "inspected": True}), "type": "result", "ok": True})
    assert "no object with the id locker_key has been seen" in calls[1] and "Every room has been entered" in calls[1], calls[1]
    assert m["kind"] == "ask" and "already been searched completely" in calls[-1], m    # it only repeats itself: ask the user

    # giving up while a room has never been entered is refused; so is picking something up before the search
    calls.clear()
    replies = iter([{"goal": [{"holding": "goggles"}], "status": "impossible", "reply": "No goggles here."},
                    {"goal": [{"holding": "goggles"}], "plan": [{"action": "explore", "target": "locker_room_a"}]}])
    b = Brain(lambda s, p, i: calls.append(p) or json.dumps(next(replies)), log=quiet)
    m = b.handle({**seeing(room, ball), "type": "command", "text": "bring me the goggles"})
    assert m["kind"] == "act" and "Not looked into yet: locker_room_a" in calls[-1], m
    b = Brain(lambda s, p, i: json.dumps({"goal": [{"holding": "goggles"}], "status": "impossible"}), log=quiet)
    m = b.handle({**seeing(entered, ball), "type": "command", "text": "bring me the goggles"})
    assert m["kind"] == "fail", m                                   # everything has been looked into: an honest no
    calls.clear()
    replies = iter([{"goal": [{"object": "basketball", "in": "locker_9"}], "plan": [
                        {"action": "move_to", "target": "basketball"}, {"action": "pick", "target": "basketball"},
                        {"action": "place", "target": "locker_9"}]},
                    {"goal": [{"object": "basketball", "in": "locker_9"}], "plan": [{"action": "look_around"}]}])
    b = Brain(lambda s, p, i: calls.append(p) or json.dumps(next(replies)), log=quiet)
    m = b.handle({**seeing(room, ball), "type": "command", "text": "ball into locker 9"})
    assert m["next"]["action"] == "look_around" and "Find it FIRST" in calls[-1], m
    w = World(seeing(fact("gym", kind="area", distance=0.0), fact("bench", distance=7.0)))
    assert w.check(clean_goal([{"robot_at": "gym"}], w.known)[0])[0] and not w.check(clean_goal([{"robot_at": "bench"}], w.known)[0])[0]

    # by its own list two objects fit the user's words and it picked one: it is asked to reconsider, once
    calls.clear()
    small, large = fact("small_red_dumbbell", pickable=True), fact("large_red_dumbbell", pickable=True)
    picked = {"things": [{"words": "the red dumbbell", "ids": ["small_red_dumbbell", "large_red_dumbbell"]}],
              "goal": [{"holding": "large_red_dumbbell"}], "plan": [{"action": "pick", "target": "large_red_dumbbell"}]}
    replies = iter([picked, {**picked, "status": "ask", "question": "Small or large?", "plan": []}])
    b = Brain(lambda s, p, i: calls.append(p) or json.dumps(next(replies)), log=quiet)
    m = b.handle({**seeing(small, large), "type": "command", "text": "hand me the red dumbbell"})
    assert m["kind"] == "ask" and "then chose one" in calls[-1], m
    both = {"things": picked["things"], "goal": [{"object": "small_red_dumbbell", "in": "bench"}, {"object": "large_red_dumbbell", "in": "bench"}],
            "plan": [{"action": "pick", "target": "small_red_dumbbell"}]}
    b = Brain(lambda s, p, i: json.dumps(both), log=quiet)
    m = b.handle({**seeing(small, large, fact("bench", holder=True)), "type": "command", "text": "all red dumbbells on the bench"})
    assert m["kind"] == "act", m                                     # all of them are used: nothing to ask

    # a step that is certain to fail is never started: the model gets the reason and plans again
    calls.clear()
    replies = iter([{"plan": [{"action": "pick", "target": "basketball"}, {"action": "open", "target": "locker_5"}]},
                    {"plan": [{"action": "pick", "target": "locker_key"}, {"action": "open", "target": "locker_5"}]}])
    b = Brain(lambda s, p, i: calls.append(p) or json.dumps(next(replies)), log=quiet)
    m = b.handle({**seeing(ball, locker_5, fact("locker_key", pickable=True)), "type": "command", "text": "ball into locker 5"})
    assert m["next"]["target"] == "locker_key" and "opens only while the robot holds locker_key" in calls[-1], m
    w = World(seeing(ball, cabinet, shelf, fact("locker_key", pickable=True), holding="basketball"))
    assert "trophy_cabinet is closed" in w.foresee([{"action": "place", "target": "trophy_cabinet_top"}])
    assert w.foresee([{"action": "open", "target": "trophy_cabinet"}, {"action": "place", "target": "trophy_cabinet"}]) is None
    assert "already holds basketball" in w.foresee([{"action": "pick", "target": "locker_key"}])
    assert w.foresee([{"action": "explore", "target": ""}, {"action": "pick", "target": "ghost"}]) is None

    # two unusable replies in a row: ask the user rather than keep them waiting
    b = Brain(lambda s, p, i: "no json here", log=quiet)
    m = b.handle({**obs, "type": "command", "text": "tidy up"})
    assert m["kind"] == "ask" and m["failures"] == 2, m

    # step cap
    b = Brain(lambda s, p, i: json.dumps({"plan": [{"action": "rotate", "degrees": 90}]}), log=quiet)
    m = b.handle({**obs, "type": "command", "text": "spin"})
    for _ in range(MAX_STEPS - 1):
        m = b.handle({**obs, "type": "result", "ok": True})
        assert m["kind"] == "act", m
    m = b.handle({**obs, "type": "result", "ok": True})
    assert m["kind"] == "fail" and m["step"] == MAX_STEPS, m

    # after_plan mode reuses the plan without calling the model, re-plans after a failure
    calls = []
    def counting(s, p, i):
        calls.append(p)
        return json.dumps({"plan": [{"action": "move_to", "target": "basketball"}, {"action": "pick", "target": "basketball"}]})
    b = Brain(counting, feedback="after_plan", log=quiet)
    b.handle({**obs, "type": "command", "text": "pick up the basketball"})
    m = b.handle({**obs, "type": "result", "ok": True})
    assert m["next"]["action"] == "pick" and len(calls) == 1, m
    b.handle({**obs, "type": "result", "ok": False, "error": "x"})
    assert len(calls) == 2

    # cancel, junk and bad numbers
    assert b.handle({"type": "cancel"})["kind"] == "done" and b.task is None
    assert parse_reply("not json", [])["error"]
    r = parse_reply(json.dumps({"plan": [{"action": "rotate", "degrees": 9000}]}), [])
    assert r["plan"][0]["degrees"] == 360.0, r
    r = parse_reply(json.dumps({"plan": [{"action": "move_to", "target": "basketball"},
                                         {"action": "pick", "target": "ghost"}]}), ["basketball"])
    assert [x["target"] for x in r["plan"]] == ["basketball", "ghost"] and not r["error"], r   # "ghost" waits, never runs
    assert parse_reply(json.dumps({"plan": [{"action": "pick", "target": "ghost"}]}), ["basketball"])["error"]
    r = parse_reply(json.dumps({"plan": [{"action": "pick", "target": "basketball"}, {"action": "fly"}]}), ["basketball"])
    assert len(r["plan"]) == 1 and not r["error"], r
    print("selftest passed")


def main():
    global BACKEND, FEEDBACK
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=os.path.join(BASE_DIR, "vlm.properties"), help="settings file")
    p.add_argument("--backend", choices=["ollama", "openrouter"], help="override 'backend' from the settings")
    p.add_argument("--model", help="override the model (Ollama: qwen, llama, or a full model tag)")
    p.add_argument("--feedback", choices=["every_step", "after_plan"],
                   help="every_step: the model looks again after every action; after_plan: only after a failure")
    p.add_argument("--test", help="plan one instruction without Unity")
    p.add_argument("--known", default="main_hall,locker_room_a,locker_room_b,locker_room_a_door,"
                                      "locker_room_b_door,basketball,ball_rack,ball_cart,bench,"
                                      "small_red_dumbbell,large_red_dumbbell,dumbbell_rack")
    p.add_argument("--holding", default="")
    p.add_argument("--image", help="robot eye screenshot for --test")
    p.add_argument("--overhead", help="overhead screenshot for --test")
    p.add_argument("--selftest", action="store_true", help="check the loop logic; needs no model")
    args = p.parse_args()

    if args.selftest:
        selftest()
        return

    try:
        props = load_properties(args.config)
    except OSError as e:
        p.error(f"cannot read settings file {args.config!r}: {e.strerror}")
    if args.backend:
        props["backend"] = args.backend
    if args.model:
        props[f"{props.get('backend', 'ollama').strip().lower()}.model"] = args.model
    FEEDBACK = args.feedback or props.get("feedback", "every_step")
    BACKEND = make_backend(props)

    if args.test:
        def b64(path):
            if not path:
                return ""
            with open(path, "rb") as f:
                return base64.b64encode(f.read()).decode()
        known = [k.strip() for k in args.known.split(",") if k.strip()]
        obs = {"type": "command", "text": args.test, "known": known, "states": known, "holding": args.holding,
               "location": "in main_hall", "image_b64": b64(args.image), "overhead_b64": b64(args.overhead)}
        print(f"using {BACKEND.name}")
        print(json.dumps(Brain(BACKEND, feedback=FEEDBACK).handle(obs), indent=2))
    else:
        asyncio.run(serve())


BACKEND = None
FEEDBACK = "every_step"

if __name__ == "__main__":
    main()
