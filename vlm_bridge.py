"""
VLM Bridge  —  Unity  <-- WebSocket -->  this script  <-- HTTP -->  vision model
                                                                   (Ollama by default; OpenRouter optional)

Closed loop, one action at a time:
  1. Unity sends the user's command plus what the robot perceives right now: its eye camera, the
     overhead camera, the objects it has seen so far (with their state), what it holds, where it is.
  2. The model answers with JSON: a status, a sentence for the user and the remaining plan.
     We validate it and send Unity only the FIRST action.
  3. Unity runs it and reports the result with fresh images. Back to 2, until the model says the task
     is done, asks the user a question, or a limit is hit (25 actions, 5 recoveries from failures).

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

MAX_STEPS = 25       # actions per command
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

SYSTEM_PROMPT = """You are the brain of a robot in an indoor sports complex. The main hall has a basketball
court, a gym area and an equipment storage wall (ball rack, racquet & bat rack, trophy shelf, trophy cabinet,
ball cart). Two locker rooms with lockers are behind doors on the east side.

Every turn you get:
- Image 1: the robot's own eye camera, showing what it is looking at right now.
- Image 2: an overhead camera above the main hall, with north at the top and east on the right. The robot is the
  small round white shape; its orange tray marks its front. The locker rooms have ceilings, so they can't be seen from above.
- KNOWN OBJECTS: every object the robot has actually seen so far, with its current state and where it is (the
  floor zone or room, or the shelf/locker it is on or in). Rooms and doors are always known. Other things may
  exist, but the robot hasn't seen them yet.
- The robot's location: its room and floor zone, the compass direction it faces, and the nearest known object.
- NOT EXPLORED YET: rooms not visited, and places whose contents haven't been checked up close.
- The task, the user's answers to your questions, and the actions taken so far with their results.

Reply with ONLY this JSON:
{"status": "working" | "done" | "ask" | "impossible",
 "reply": "<one short sentence for the user>",
 "question": "<only when status is ask>",
 "plan": [<remaining steps; only the FIRST runs, then you see the result and plan again>]}

Steps:
  {"action": "move_to", "target": "<id>"}     walk to an object, or into a room
  {"action": "pick", "target": "<id>"}        pick up a small object (it holds one thing at a time)
  {"action": "place", "target": "<id>"}       put the held object on/in <id>; a rack picks a free shelf, or name one (ball_rack_top)
  {"action": "open", "target": "<id>"}        open a door, locker or cabinet
  {"action": "close", "target": "<id>"}
  {"action": "explore", "target": "<room or furniture id>"}  search where the thing most likely is: opens the
                                              room's door, walks in and looks around, opens its lockers one by one,
                                              or looks closely at a shelf/cart. Leave out "target" to search the nearest place.
  {"action": "look_around"}                   turn a full circle on the spot
  {"action": "rotate", "degrees": <number>}   turn in place, positive = right
  {"action": "wait", "seconds": <number>}

Rules:
1. Use ids EXACTLY as written in KNOWN OBJECTS. Never invent an id. If the thing you need isn't known yet (an
   object, or a named place such as "locker 5"), it is probably somewhere under NOT EXPLORED YET: explore where it
   is most likely (balls: storage wall and ball cart; personal things, keys, medals: lockers; lockers: BOTH locker
   rooms) and repeat explore until it shows up in KNOWN OBJECTS. Exploring needs no permission: don't ask first.
   Never use a different object in place of the one asked for (a trophy is not a medal).
2. The robot holds one thing at a time. Place what it holds before picking up something else.
3. Open a closed locker or cabinet before taking things out or putting things in, and close a cabinet again when
   you are finished with it. A locked locker ("locked, needs X")
   opens only while the robot holds X. If X is in KNOWN OBJECTS, fetch it first, then open the locker (the key stays
   in the lock). If X is NOT known yet, don't start searching on your own: return status "ask", say what is locked
   and which key is missing, and ask whether the user knows where it is or wants you to search. Then do what they answer.
4. A closed door blocks the way into its room; open it first.
5. Use the state lines and the images to check that the last step worked. When the whole task is finished,
   return status "done" with an empty plan.
6. If the instruction could mean more than one known object and the images don't settle it (for example "the red
   dumbbell" when two red dumbbells are known), return status "ask" with a short question and an empty plan.
   Don't ask when only one object fits.
7. If a step failed, read the error and try ONE different step that fixes the cause. Never repeat a step that just
   failed unchanged. If you can't see a step that fixes it, return status "ask" and ask the user what to do.
   Don't ask about things you can simply do (opening a closed door or locker, walking closer, exploring), and
   never ask a question the user has already answered. After a step succeeds, go on to the NEXT step of the task;
   repeating a step that already succeeded changes nothing.
8. If the task can't be done (the object still isn't found after exploring, or it makes no sense), return status
   "impossible" with an empty plan and say why.

Example. TASK: put the basketball on the ball rack. KNOWN OBJECTS include basketball and ball_rack.
{"status": "working", "reply": "Taking the basketball to the ball rack.", "plan": [
  {"action": "move_to", "target": "basketball"}, {"action": "pick", "target": "basketball"},
  {"action": "move_to", "target": "ball_rack"}, {"action": "place", "target": "ball_rack"}]}

Example. TASK: fetch the swimming goggles. Nothing like that is known yet, and goggles would be kept in a locker.
{"status": "working", "reply": "I haven't seen goggles yet, so I'll check the locker rooms.", "plan": [
  {"action": "explore", "target": "locker_room_a"}]}

Example. TASK: hand me the red dumbbell. KNOWN OBJECTS include small_red_dumbbell and large_red_dumbbell.
{"status": "ask", "reply": "There are two red dumbbells.", "question": "Do you want the small or the large red dumbbell?", "plan": []}
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
    def __init__(self, url, model, timeout=300, num_ctx=8192, keep_alive="5m", num_batch=128):
        self.url, self.model = url, model
        self.name = f"ollama:{model}"
        self.timeout, self.num_ctx, self.keep_alive = timeout, num_ctx, keep_alive
        self.num_batch = num_batch

    def __call__(self, system, user_text, images):
        if self.model.split(":", 1)[0] == "llama3.2-vision" and len(images) > 1:
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
                             num_batch=positive_int(options, "ollama.num_batch", 128))
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


def clean_step(step, known):
    """One raw step -> (clean action dict, None) or (None, error)."""
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
        if t not in known:
            return None, (f"'{t}' is not a known object; use an id from KNOWN OBJECTS, "
                          f"or explore to find it")
        clean["target"] = known[t]
    elif param:
        try:
            value = float(step.get(param, 0))
        except (TypeError, ValueError):
            return None, f"bad {param} in {name}"
        clean[param] = max(-360.0, min(360.0, value)) if param == "degrees" else max(0.0, min(10.0, value))
    return clean, None


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
    """Raw VLM text -> {status, reply, question, plan, error}. The plan keeps the valid steps up to
    the first bad one; an unusable first step is an error."""
    known = {norm(k): k for k in known_ids}
    out = {"status": "working", "reply": "", "question": "", "plan": [], "error": ""}
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
    out.update(status=status, reply=str(data.get("reply", "")).strip(),
               question=str(data.get("question", "")).strip())

    if status in ("ask", "done", "impossible"):
        if status == "ask" and not (out["question"] or out["reply"]):
            out["error"] = "status was ask but there was no question"
        return out

    for i, step in enumerate(steps):
        clean, err = clean_step(step, known)
        if err:
            if i == 0:
                out["error"] = err
            break
        out["plan"].append(clean)
    if not out["plan"] and not out["error"]:
        out["status"] = "done"
    return out


def show(action):
    if action["action"] == "rotate":
        return f"rotate {action['degrees']:g} deg"
    if action["action"] == "wait":
        return f"wait {action['seconds']:g} s"
    return f"{action['action']} {action['target']}".strip()


# ---------------------------------------------------------------- the loop

class Task:
    def __init__(self, command):
        self.command = command
        self.answers = []        # (question, answer)
        self.history = []        # one line per action or planner problem
        self.steps = 0
        self.failures = 0
        self.question = None     # waiting for the user's answer
        self.plan = []           # remaining planned steps (used in after_plan mode)
        self.streak = 0          # failed actions in a row
        self.blocked = {}        # step -> error, for steps that failed since the last success or user answer
        self.asked = set()       # missing keys the user has already been asked about
        self.just_done = None    # the step that succeeded last turn, to catch a model that repeats it for no reason
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
            t.history.append(line)
            t.current = None
            return self.decide(obs, last_ok=not error, last_error=error)
        return self.msg("done", error=f"unknown message type '{kind}'")

    def blocker(self, t, known, error):
        """A question for the user when the robot is blocked and shouldn't keep trying on its own, else None.
        It may fix an obvious problem itself, but a missing key or repeated dead ends go straight to the user."""
        key = re.search(r"is locked; it needs (\w+)", error)
        if key and norm(key.group(1)) not in {norm(k) for k in known} and key.group(1) not in t.asked:
            t.asked.add(key.group(1))
            return (f"{error.split(';')[0].strip().capitalize()} and I haven't seen {key.group(1)} yet. "
                    f"Do you know where it is, or should I search for it?")
        if t.streak >= STUCK_AFTER:
            return f"I'm stuck after {t.streak} tries. The last problem: {error}. What should I do?"
        return None

    def ask(self, question, raw=""):
        self.task.question = question
        return self.msg("ask", reply=question, raw=raw)

    def decide(self, obs, last_ok=False, last_error=""):
        t = self.task
        known = obs.get("known") or []
        rejected = 0
        while True:
            if t.steps >= MAX_STEPS:
                return self.finish("fail", f"I used all {MAX_STEPS} actions without finishing, so I'm stopping here.")
            if t.failures > MAX_FAILURES:
                return self.finish("fail", f"I ran into {t.failures} problems in a row and can't find a way to "
                                           f"finish this, so I'm stopping.")

            question = last_error and self.blocker(t, known, last_error)
            last_error = ""
            if question:
                return self.ask(question)

            if self.feedback == "after_plan" and last_ok and t.plan:
                step, err = clean_step(t.plan[0], {norm(k): k for k in known})
                if not err:
                    t.plan.pop(0)
                    return self.act(step, reply="", raw="")
                t.plan = []

            prompt = build_prompt(t, obs)
            images = [b for b in (obs.get("image_b64"), obs.get("overhead_b64")) if b]
            try:
                raw = self.vlm(SYSTEM_PROMPT, prompt, images)
            except Exception as e:  # model server down, out of free requests, timeout...
                return self.finish("fail", "The vision model is unavailable right now.", error=str(e))

            r = parse_reply(raw, known)
            if r["error"]:
                t.failures += 1
                t.history.append(f"(your last reply was rejected: {r['error']})")
                self.log(f"rejected reply: {r['error']}\n  raw: {raw}")
                last_ok = False
                rejected += 1
                if rejected >= 2:   # don't keep the user waiting while the model flounders
                    return self.ask("I'm not sure what to do next. Can you tell me the next step, or say it another way?", raw=raw)
                continue

            if r["status"] == "ask":
                question = r["question"] or r["reply"]
                answered = next((ans for q, ans in t.answers if norm(q) == norm(question)), None)
                if answered is not None:   # asking again what the user already answered would loop forever
                    t.failures += 1
                    t.history.append(f'(you already asked "{question}" and the user answered "{answered}". '
                                     f"Don't ask it again: act on that answer now.)")
                    last_ok = False
                    rejected += 1
                    if rejected >= 2:
                        return self.ask("I'm not sure what to do next. Can you tell me the next step, or say it another way?", raw=raw)
                    continue
                return self.ask(question, raw=raw)
            if r["status"] in ("done", "impossible"):
                default = "Done." if r["status"] == "done" else "I can't do that."
                return self.finish("done" if r["status"] == "done" else "fail", r["reply"] or default, raw=raw)

            first = r["plan"][0]
            if first["action"] in ("move_to", "open", "close") and show(first) == t.just_done:
                # The step just succeeded and doing it again changes nothing: a model stuck on a no-op.
                t.failures += 1
                t.history.append(f"(you proposed '{show(first)}' again, but it already succeeded and repeating it "
                                 f"changes nothing. Give the NEXT step of the task, or status done if it is finished.)")
                self.log(f"rejected no-op repeat: {show(first)}")
                last_ok = False
                rejected += 1
                if rejected >= 2:
                    return self.ask("I'm not sure what to do next. Can you tell me the next step, or say it another way?", raw=raw)
                continue
            repeat = t.blocked.get(show(r["plan"][0]))
            if repeat:   # the same step that just failed, with nothing changed since: ask instead of looping
                t.failures += 1
                return self.ask(f"I'm stuck: {repeat}. What should I do?", raw=raw)

            t.plan = r["plan"][1:]
            return self.act(r["plan"][0], reply=r["reply"], raw=raw)

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


def build_prompt(t, obs):
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
    lines.append(f"ACTIONS SO FAR ({t.steps} of {MAX_STEPS} used, {t.failures} of {MAX_FAILURES} problems):")
    lines += [f"  {i + 1}. {h}" for i, h in enumerate(t.history)] or ["  none yet"]
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
    m = b.handle({**obs, "type": "result", "ok": True})
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
    lock = {**obs, "known": ["locker_5", "main_hall"]}
    b = Brain(lambda s, p, i: calls.append(p) or json.dumps({"plan": [{"action": "open", "target": "locker_5"}]}), log=quiet)
    m = b.handle({**lock, "type": "command", "text": "open locker 5"})
    m = b.handle({**lock, "type": "result", "ok": False, "error": "locker_5 is locked; it needs locker_key"})
    assert m["kind"] == "ask" and "locker_key" in m["reply"] and len(calls) == 1, (m, len(calls))
    # ... but when the key is already known the robot goes on by itself
    b = Brain(scripted({"plan": [{"action": "open", "target": "locker_5"}]},
                       {"plan": [{"action": "pick", "target": "locker_key"}]}), log=quiet)
    seen = {**lock, "known": lock["known"] + ["locker_key"]}
    m = b.handle({**seen, "type": "command", "text": "open locker 5"})
    m = b.handle({**seen, "type": "result", "ok": False, "error": "locker_5 is locked; it needs locker_key"})
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
    replies = iter([{"status": "ask", "question": "Should I explore locker_room_b?", "plan": []},
                    {"status": "ask", "question": "Should I explore locker_room_b?", "plan": []},
                    {"status": "working", "plan": [{"action": "look_around"}]}])
    b = Brain(lambda s, p, i: seen_prompts.append(p) or json.dumps(next(replies)), log=quiet)
    m = b.handle({**obs, "type": "command", "text": "put the dumbbell in locker 5"})
    assert m["kind"] == "ask", m
    m = b.handle({**obs, "type": "answer", "text": "yes, go ahead"})
    assert m["kind"] == "act" and "already asked" in seen_prompts[-1] and "yes, go ahead" in seen_prompts[-1], m

    # a step that just succeeded is not run again: the model is told to move on (it was looping on a no-op)
    seen_prompts = []
    replies = iter([{"plan": [{"action": "move_to", "target": "ball_rack"}]}, {"plan": [{"action": "move_to", "target": "ball_rack"}]},
                    {"plan": [{"action": "place", "target": "ball_rack"}]}])
    b = Brain(lambda s, p, i: seen_prompts.append(p) or json.dumps(next(replies)), feedback="every_step", log=quiet)
    m = b.handle({**obs, "type": "command", "text": "put the basketball on the rack"})
    m = b.handle({**obs, "type": "result", "ok": True})
    assert m["kind"] == "act" and m["next"]["action"] == "place" and "already succeeded" in seen_prompts[-1], m

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
