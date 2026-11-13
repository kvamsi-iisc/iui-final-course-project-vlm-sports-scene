# VLM → Unity Robot Control — Sports Room

```
User types a command (Unity UI)
   │  + robot eye image + overhead image + objects the robot has SEEN (with their state)
   ▼
CommandClient.cs ──WebSocket──► vlm_bridge.py ──HTTP──► Ollama (Qwen / Llama vision) or optional OpenRouter, set in vlm.properties
                                    │ validates the JSON plan: allowed actions, known ids only
   ◄──────── {kind, next action, reply, raw} ┘
   ▼
RobotAgent.cs runs that ONE action (walk, pick, place, open, explore...) and reports the result
   │  + fresh images and state
   └──► back to the bridge, which continues the plan or asks the model again ... until done, a question, or a limit
```

## How the robot thinks
- **Feedback loop:** By default, `feedback = after_plan` executes the planned steps without a model call between successful actions. Unity still checks each action; the model is asked again if a step fails, if the plan reaches a step it cannot run yet, or if the plan ends with the goal not reached. Use `--feedback every_step` for visual checking after every action.
- **The model states the goal, the world checks it:** every reply carries a `goal`, the task as checkable facts
  (`{"object": "gold_medal", "in": "trophy_cabinet"}`, `{"holding": "soccer_ball"}`, `{"closed": "trophy_cabinet"}`).
  Understanding the user's words is the model's job; the bridge only compares that goal with the world data Unity
  sends (`facts`). There are no keyword rules about commands in the bridge, so synonyms and new phrasings need no code.
- **Find first, then act:** a plan may contain search steps followed by steps for a thing not seen yet
  (`look_around`, `explore locker_room_a`, `pick gold_medal`). Steps naming an unseen object wait; the search stops
  and the plan continues the moment that exact id is seen. `explore <room>` is repeated until the room is searched.
- **No walking into certain failures:** before a step runs the bridge checks the world data: hand already full,
  container closed, locker locked without the key in the hand. Such a plan goes back to the model with the reason.
- **Limits:** at most 40 actions per command, and it recovers from at most 5 failed steps or unusable replies before stopping politely.
- **Seen-only knowledge:** an object becomes known only once it is really visible in the eye or overhead camera (in view, not
  blocked, at least 4 pixels big). Rooms and doors are always known. The locker rooms are roofed, so the overhead camera can't see into them.
- **Exploring:** `explore` searches the most likely place: opens a room's door, walks in and looks around, opens its lockers one by
  one, or looks closely at a shelf or cart. The model can aim it at a room or piece of furniture.
- **Asking back:** if a command fits more than one object ("the red dumbbell"), it asks you; your next message is the answer.
- **Stuck → ask:** the robot may fix an obvious problem itself (open a closed door, fetch a key it has seen), but it
  asks you instead of looping when a locked locker's key hasn't been seen, when the model proposes the step that just
  failed again, after 3 failed actions in a row (`STUCK_AFTER`), or after two unusable model replies. Your answer
  continues the same task.
- **"Done" is checked:** the robot may say it is finished only when every entry of the goal holds in the world
  data. A wrong "done" is refused with the reason; after three the robot reports a failure instead of a success.
  When a plan ends and the goal holds, the task finishes at once, without another model call.
- **No loops:** the bridge also refuses a question you have already answered and a `move_to`/`open`/`close` that
  just succeeded, and tells the model to move on.
- **Model comparison:** `EVALUATION.md` compares Qwen3-VL 8B and Llama 3.2 Vision 11B on eight end-to-end tasks
  (response times, plan and question quality, behaviour when stuck); raw traces are in `evaluation/`.
- **Stop:** type `stop` while it works; it stops after the current step.
- The raw VLM JSON of every turn is shown in the log, next to live thumbnails of exactly what the model sees.

## What viewers see
- **Cut-away model:** the audience camera looks from the south with north at the top, like the overhead map. The south
  wall, the locker-room ceilings and the backs of the storage racks are hidden from it only; the robot still sees them.
- **Robot awareness overlays** (audience camera only, never in the images sent to the VLM): a cyan line along the
  planned route, a blue fan for the area the eye camera covers (cut off by walls), a yellow ring around the object
  being worked on, a green ping when something is noticed for the first time, and a caption with the current action.
  Switch any of them off on the robot's `RobotVisuals` component.
- **Status lights** on the robot: cyan idle, pulsing amber thinking, green acting, violet waiting for an answer, red stopped.
- **Spatial words for the VLM:** every state line says where the thing is (`on the floor, basketball court`, `in gym`),
  and the robot reports its room, floor zone and compass heading. The overhead image has north at the top.

## Files
| File | Role |
|---|---|
| `vlm_bridge.py` | Prompt, VLM call, **validation** of VLM output, the step-by-step loop and its limits, WebSocket server |
| `tools/run_local.py` | Starts the Ollama version matching the selected model, then the bridge; cleans up on exit |
| `tools/ollama.sh` | Local Ollama CLI helper for model downloads and inspection |
| `SportsRobot/Scripts/CommandClient.cs` | Unity UI, camera capture, sends observations, runs each action, shows VLM output |
| `SportsRobot/Scripts/RobotAgent.cs` | Executes each command in the engine, including explore and look_around |
| `SportsRobot/Scripts/Perception.cs` | What the robot has seen, visited and checked up close |
| `SportsRobot/Scripts/Interactable.cs` | Objects the VLM can refer to; shelves, slots, lockers, keys, doors |
| `SportsRobot/Scripts/RoomArea.cs` | Rooms of the floor plan and their doors |
| `SportsRobot/Scripts/FloorZone.cs` | Named floor areas (basketball court, gym, equipment storage) used in the robot's location report |
| `SportsRobot/Scripts/RobotVisuals.cs` | What viewers see of the robot's awareness: status lights, route line, view cone, target ring, "noticed" pings |
| `SportsRobot/Scripts/AudienceCamera.cs` | Viewers' camera; keeps the whole building framed whatever the Game window size |
| `SportsRobot/Editor/SportsRoomBuilder.cs` | One-click scene generator (menu **Sports Robot ▸ Scene Builder**) |
| `SportsRobot/Editor/SportsRoomDecor.cs` | The scene's look (second half of the generator): floor textures, court lines, signs, lighting, the robot model |
| `SportsRobot/Layouts/sports_room.json` | The room design: rooms, doors, furniture, items, robot start. Edit this, rebuild. |
| `tools/preview_layout.py` | Renders a layout without Unity (item models, furniture close-ups, floor plan) into `previews/`: `python3 tools/preview_layout.py SportsRobot/Layouts/sports_room.json previews` |

## Command vocabulary (the mapping)
| VLM output | Unity behaviour |
|---|---|
| `{"action":"move_to","target":"X"}` | NavMeshAgent walks to X's approach point (or the middle of room X) |
| `{"action":"pick","target":"X"}` | X is parented to the robot's hand |
| `{"action":"place","target":"Y"}` | held object goes into a free slot on Y (a rack picks a free shelf) |
| `{"action":"open"/"close","target":"Y"}` | Y's door hinge rotates; a locked locker opens only with its key in hand |
| `{"action":"explore","target":"Y"}` | one search step in room Y / at furniture Y (target optional); the bridge repeats it for a room until the room is searched |
| `{"action":"look_around"}` | turns a full circle |
| `{"action":"rotate","degrees":N}` | robot turns in place |
| `{"action":"wait","seconds":N}` | pause |

## 1. Python side: local vision by default

The external **`vlm.properties`** file selects the backend and model. Ollama is the default;
no OpenRouter key is needed for local use. Edit this file and **restart the launcher** to switch:

```properties
backend = ollama
ollama.model = qwen
```

| `ollama.model` | Local model | Camera handling |
|---|---|---|
| `qwen` (initial default) | `qwen3-vl:8b-instruct` | Eye and overhead images sent separately |
| `llama` | `llama3.2-vision:11b` | Both views combined into one labeled image |

Only the selected model answers each command. The aliases are defined by `ollama.models.qwen`
and `ollama.models.llama` in the same file. A full installed vision-model tag also works, for
example `ollama.model = qwen3-vl:4b-instruct`. `ollama.timeout`, `ollama.num_ctx`, and
`ollama.keep_alive` control request timeout, context size, and time kept in memory; `ollama.num_batch` controls inference batch size.

### Start the project

Both models and the required macOS runtimes have been downloaded in this workspace. Install
Python dependencies on a fresh checkout, then use the launcher:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python vlm_bridge.py --selftest
.venv/bin/python tools/run_local.py
```

The launcher starts the correct local Ollama runtime and the bridge. To switch models,
press **Ctrl+C**, edit `ollama.model` in `vlm.properties`, and run the launcher again.
It shuts down processes it started, and never stops a pre-existing server. If port 11434
is occupied by an incompatible Ollama version, stop that server before launching.

### Runtime compatibility and downloads

This Mac requires two runtime versions: Qwen uses **Ollama 0.40.16** in `.local/ollama/`;
Llama uses **Ollama 0.24.0** in `.local/ollama-0.24.0/`. The launcher selects one automatically.
In local tests, 0.40.16 rejected Llama's `mllama` architecture, while 0.24.0's Metal runner
failed on the current split Qwen vision files. Keep these versions separate.

For a fresh checkout, download `ollama-darwin.tgz` from the official
[0.40.16 release](https://github.com/ollama/ollama/releases/tag/v0.40.16) and
[0.24.0 release](https://github.com/ollama/ollama/releases/tag/v0.24.0), verify against each
release's SHA-256 digest, and extract each archive into its directory above.
Both runtimes share `.local/ollama-models/`. To populate the model store:

```bash
# Terminal 1: temporary server for downloads; stop with Ctrl+C afterwards
bash tools/ollama.sh serve
# Terminal 2: pull both models and list them
bash tools/ollama.sh pull-defaults
```

The launcher binds to `127.0.0.1:11434`, disables Ollama cloud features, and keeps at most
one model loaded. Ollama creates its own key files under `~/.ollama` on first start.
Exclude `.local/` and `.venv/` from submission archives. For layout previews, separately
install `numpy matplotlib`.

### Test or override the model

Run these with other bridge/Ollama instances stopped; the launcher handles their lifecycle:

```bash
.venv/bin/python tools/run_local.py --model qwen --test "put the basketball on the ball rack" \
  --image previews/unity_eye_start.png --overhead previews/unity_overhead.png
.venv/bin/python tools/run_local.py --model llama --test "put the basketball on the ball rack" \
  --image previews/unity_eye_start.png --overhead previews/unity_overhead.png
.venv/bin/python tools/run_local.py --model llama
.venv/bin/python tools/run_local.py --config /path/to/vlm.properties
```

Command-line flags override the file for one run. `--selftest` checks backend selection,
image handling, error reporting, validation, and the feedback loop without a model or Unity.
The original `vlm_bridge.py` entry point remains available for an already-running compatible
Ollama server or a custom `ollama.url`.

Qwen is the faster default on this 16 GB M2 Pro. The final two-camera smoke tests took about 35 seconds
for Qwen and 62 seconds for Llama, including loading and image processing. Llama uses
`ollama.llama.num_ctx = 4096` and `ollama.llama.num_batch = 32` to reduce memory pressure.
Latency depends on available memory and other apps. Adjust `ollama.timeout` for slower calls.

### Optional OpenRouter

Set `backend = openrouter` in `vlm.properties`, or run:

```bash
python3 vlm_bridge.py --backend openrouter
```

Configure `openrouter.model` and optional `openrouter.fallback_models` in the file. Supply
`OPENROUTER_API_KEY` or put the key in ignored `creds/openRouter.txt`; the environment wins.
Never include `creds/` in submission archives. OpenRouter receives camera images and commands
only when explicitly selected. There is **no automatic cloud fallback** from Ollama.
Cloud model availability, quotas, and pricing depend on the provider.

## 2. Unity scene (Unity 6 LTS, Universal 3D template)
1. **Packages:** install **AI Navigation** (Window ▸ Package Manager ▸ Unity Registry) and import TMP Essentials
   (Window ▸ TextMeshPro ▸ Import TMP Essential Resources).
2. Copy the `SportsRobot/` folder into the project's `Assets/` folder.
3. Open **Sports Robot ▸ Scene Builder**, optionally drop a robot model into *Robot model*, click **Build scene**.
   It generates `Assets/SportsRobot/Scenes/SportsRoom.unity` with rooms, doors, furniture, items, robot,
   cameras, chat UI and a baked NavMesh, and lists any layout mistakes it found.
4. Run `.venv/bin/python tools/run_local.py`, press **Play**, type a command, press Enter.

To change the room, edit `SportsRobot/Layouts/sports_room.json` (instructions are at the top of the file) and click **Build scene** again.

## Demo commands to try
- `put the basketball on the ball rack` (short plan, visual check after each step)
- `put all three tennis balls on the top shelf of the ball rack` (multi-step; it has to look into the ball cart up close first)
- `hand me the red dumbbell` → asks: small or large? Answer `the large one`.
- `put the blue water bottle in the trophy cabinet` (glass cabinet: open, place, close)
- `get the gold medal and put it in the trophy cabinet` (the hard one: explores the locker rooms, finds locker_5 locked,
  finds locker_key in another locker, unlocks locker_5, takes the medal)
- `bring me the swimming goggles` → explores, then says it couldn't find any
- `stop` while it is working

## Tips
- First call is slow while the model loads; send one warm-up command before presenting.
- The default `after_plan` mode reduces model calls. For debugging, use `--feedback every_step`; each successful action then incurs another model call.
- `temperature: 0` keeps answers repeatable for the demo.
- If plans fail often, add another example to `SYSTEM_PROMPT` matching that case.
- Record a backup video of a successful run.
