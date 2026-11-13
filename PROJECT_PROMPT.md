# Project Prompt: Vision-Language Robot Control in a Unity Sports Complex

Use this prompt to brief an AI assistant, a teammate or a grader on the whole workspace. It describes what the
system must do, the sports setting in full, the contract between the vision-language model (VLM) and the robot,
and, above all, how the demonstration must prove that the VLM is **accurate**: grounded in what the robot
actually sees, never hallucinating, and verifying every step.

---

## 1. Goal

Build and demonstrate an intelligent user interface in which a person types a natural-language command
("put the basketball on the ball rack") and a simulated robot in a Unity sports complex carries it out.

- A **VLM** is the robot's brain. Every turn it receives the robot's camera images, the list of objects the robot
  has seen, and the command, and it replies with a strict JSON plan of actions.
- A **Python bridge** (`vlm_bridge.py`) calls the model, **validates** its JSON (allowed actions only, known object
  ids only, sane parameters), and sends one action at a time to Unity over a WebSocket (`localhost:8765`).
- A **Unity NavMesh robot** executes each action physically (walk, pick, place, open, close, rotate, wait,
  look around, explore) and reports success or a precise error, plus fresh images and state.
- The **raw VLM JSON** of every turn is shown on screen, next to live thumbnails of exactly what the VLM saw.
- At least four commands must work end to end, including multi-step ones. Extra credit comes from visual grounding,
  multi-step planning, a closed feedback loop, and robust error handling — all of which are in scope.

---

## 2. The sports setting

A single-storey indoor **Sports Complex**, generated from `SportsRobot/Layouts/sports_room.json` by the Unity
editor tool `SportsRobot/Editor/SportsRoomBuilder.cs`. Coordinates are metres on the floor as `[x, z]`
(x grows east, z grows north). Walls are 3.5 m high, warm off-white (`#E9E4DA`). Every object below is modelled
realistically at real-life size (seams on balls, plates on dumbbells, strings on rackets, a wire basket on the
ball cart), so the VLM can recognise things from pixels, not from text labels.

### 2.1 Rooms and doors

| Room | Footprint `[x, z, w, d]` | Notes |
|---|---|---|
| `main_hall` | `[0, 0, 30, 20]` | Open hall with a wooden floor; fully visible from the overhead camera |
| `locker_room_a` | `[30, 10, 8, 10]` | North-east, **roofed** (overhead camera cannot see inside), blue lockers |
| `locker_room_b` | `[30, 0, 8, 10]` | South-east, **roofed**, red lockers |

| Door | Position | Connects |
|---|---|---|
| `locker_room_a_door` (blue, signed "LOCKER ROOM A") | `[30, 15]`, 1.6 m wide | main_hall ↔ locker_room_a |
| `locker_room_b_door` (red, signed "LOCKER ROOM B") | `[30, 5]`, 1.6 m wide | main_hall ↔ locker_room_b |

Doors start **closed** and block walking until opened.

### 2.2 Zones of the main hall

- **Basketball court** (north-west, orange floor, `[1, 10.5, 13, 9]`): `basketball_hoop` on the north wall facing
  south; three training hurdles (`hurdle_1`, `hurdle_2`, `hurdle_3`); a row of `orange_cone_1..3` at z = 16.5;
  the **`basketball`** lies on the court at `[7.5, 16.5]`.
- **Gym** (north-east, dark floor, `[16, 11, 13.5, 8.5]`): `treadmill_1`, `treadmill_2`, `exercise_bike`,
  `rowing_machine`, `bench_press` along the north wall; `squat_rack` facing west; a two-level black
  `dumbbell_rack`; `blue_mat` and `green_mat` on the floor.
- **Equipment storage wall** (south, `[1, 0.2, 18, 3.6]`), all furniture facing north, west to east:
  `ball_rack` (3 shelves), `bat_rack` (2 shelves), `trophy_shelf` (3 shelves, open), `trophy_cabinet`
  (2 shelves behind a **glass door**, so its contents are visible but it must be opened to reach them).
  In front of it stands the `ball_cart` (wire basket).
- **Open floor**: `football_goal` on the west wall facing east, the `soccer_ball` at `[3.8, 5.0]`, and a
  `bench` at `[14, 7.5]` facing north.

Racks and cabinets expose shelf targets: `<id>_bottom`, `<id>_middle`, `<id>_top` (`_middle` only with 3 levels).

### 2.3 Locker rooms

- **Locker room A:** `locker_1`, `locker_2`, `locker_3` against the east wall facing west; `changing_bench_a`.
- **Locker room B:** `locker_4`, `locker_5` (**locked; opens only while the robot holds `locker_key`**),
  `locker_6`; `changing_bench_b`.
- All lockers start closed. Their contents are unknown until the robot opens them and looks inside.

### 2.4 Items and where they start

| Item id | Description | Starts on / at |
|---|---|---|
| `basketball` | Orange basketball, 24 cm, black seams | Court floor `[7.5, 16.5]` |
| `spare_basketball` | Second basketball | `ball_rack_top` |
| `volleyball` | Yellow with blue panels, 21 cm | `ball_rack_middle` |
| `handball` | Blue with yellow panels, 18 cm | `ball_rack_middle` |
| `rugby_ball` | Brown oval ball | `ball_rack_bottom` |
| `soccer_ball` | White with black patches, 22 cm | Floor `[3.8, 5.0]` |
| `tennis_ball_1..3` | Fluorescent yellow-green, 6.7 cm | Inside `ball_cart` (visible only up close) |
| `small_red_dumbbell` | Red, small | `dumbbell_rack_top` |
| `large_red_dumbbell` | Red, large | `dumbbell_rack_bottom` |
| `blue_dumbbell` | Blue, medium | `blue_mat` |
| `blue_water_bottle`, `red_water_bottle` | Sports bottles | `bench` |
| `green_water_bottle` | Sports bottle | Inside `locker_1` |
| `tennis_racket` (navy), `badminton_racket` (purple) | Rackets | `bat_rack_top` |
| `cricket_bat` (willow), `baseball_bat` (brown) | Bats | `bat_rack_bottom` |
| `orange_cone_1..3` | Training cones | Court floor |
| `gold_trophy`, `silver_trophy`, `bronze_trophy` | Trophies | `trophy_shelf_top / _middle / _bottom` |
| `gold_medal` | Gold medal, red ribbon | Inside locked `locker_5` |
| `locker_key` | Silver key with tag | Inside `locker_2` |

There are **no swimming goggles** anywhere: that command exists to test an honest "impossible" answer.

### 2.5 Robot and cameras

- The robot starts at `[15, 9.5]` in the middle of the hall, facing north. It walks with a NavMeshAgent, holds
  **one object at a time** in its hand, and has a head-mounted **eye camera** that tilts down to see shelves,
  carts and locker floors. Its own body is on a hidden layer so it never blocks its own view.
- **Image 1 – robot eye** (640×480): what the robot is looking at right now.
- **Image 2 – overhead** (640×480): a camera above the main hall. The locker rooms are roofed, so they appear as
  ceilings; the robot must physically go in and look.
- An audience camera and a chat UI with a scrolling log, a status line and both thumbnails are shown in the Game view.

---

## 3. What the robot knows (seen-only knowledge)

The VLM is never given the full layout. An object becomes **known** only when the robot has really seen it:
inside a camera's view, not blocked by walls or doors (checked by raycast), and at least 4 pixels tall in the
image. Rooms and doors are always known. A place counts as **checked up close** once the robot has seen it from
within 4 m; until then its description says "(maybe more, not checked up close)".

At the start about 32 objects are known: the hall furniture and the items visible from above. Nothing inside the
lockers, and not the tennis balls deep in the cart, is known yet.

Every turn the VLM receives:
1. The command, any answers the user gave to its questions, and the actions taken so far with their results.
2. `KNOWN OBJECTS`: every seen object with its live state, e.g. `locker_5: locker, closed, locked (needs locker_key)`,
   `ball_rack_top: holds spare_basketball`, `holding: basketball`.
3. `NOT EXPLORED YET`: unvisited rooms and places not yet checked up close.
4. Image 1 (eye) and Image 2 (overhead).

---

## 4. VLM output contract

The VLM must reply with **only** this JSON:

```json
{"status": "working" | "done" | "ask" | "impossible",
 "reply": "<one short sentence for the user>",
 "question": "<only when status is ask>",
 "plan": [<remaining steps; only the FIRST runs, then the VLM sees the result and plans again>]}
```

| Action | Parameters | Unity behaviour |
|---|---|---|
| `move_to` | `target` | Walk to the object's approach point, or into the middle of a room |
| `pick` | `target` | Put a small object in the hand (fails if already holding, or if it is behind a closed door) |
| `place` | `target` | Put the held object on/in a target; a rack chooses a free shelf unless one is named |
| `open` / `close` | `target` | Door, locker or cabinet; a locked locker opens only while holding its key, and the key stays in the lock |
| `explore` | optional `target` | One search step in a room or at a piece of furniture: open the room's door, walk in and look around, open its lockers one by one, or look closely at a shelf or cart |
| `look_around` | — | Turn a full circle in four 90° steps |
| `rotate` | `degrees` (clamped to ±360) | Turn in place, positive = right |
| `wait` | `seconds` (clamped to 0–10) | Pause |

Loop limits: at most **25 actions** per command and at most **5 recoveries** from failed steps or unusable
replies, then the robot stops politely. Typing `stop` cancels after the current step.

---

## 5. Accuracy of the VLM demonstration (the most important section)

The demonstration is judged on whether the VLM's decisions are **correct, grounded and verifiable**, not on
how fluent its sentences are. Every design choice below serves that goal; keep all of them.

### 5.1 Grounding: act only on what is real
- The VLM may use **only** ids that appear in `KNOWN OBJECTS`, spelled exactly. The bridge rejects any step whose
  action is not in the allowed list or whose target is not known, and feeds the error back. Never weaken this check.
- If the needed object is not known, the VLM must **explore** the most plausible place (balls: storage wall and
  ball cart; keys, medals, personal items: lockers) and keep exploring until the object appears — it must not guess
  an id or assume where something is.
- The images matter: colours, sizes and positions in the eye and overhead images must be used to tell objects
  apart and to confirm that an action worked (the ball is now on the rack, the locker door is open).

### 5.2 Closed-loop verification
- With `feedback = every_step` (the demo setting) the VLM sees fresh images and state **after every action** and
  re-plans. A step is considered done only when the state lines and images confirm it.
- After a failure the VLM must read the exact error (`locker_5 is locked; needs locker_key`, `already holding
  basketball; place it first`, `trophy_cabinet is closed`) and choose a **different** action. Repeating a failed
  step unchanged is a defect.
- `status: "done"` is allowed only when the final state proves the task is complete, with an empty plan.

### 5.3 Honesty: ask or refuse instead of guessing
- **Ambiguity → ask.** If a command fits more than one known object and the images do not settle it ("the red
  dumbbell" while both red dumbbells are known), the VLM returns `status: "ask"` with one short question and an
  empty plan. It must not ask when only one object fits.
- **Not found → impossible.** If the object is still unknown after a sensible search (e.g. swimming goggles), it
  returns `status: "impossible"` and says why. It never fabricates success.

### 5.4 Robustness of the output
- Replies are parsed defensively: JSON inside code fences or surrounding text is extracted; the valid prefix of a
  plan is kept and the first invalid step is reported back; an invalid first step counts as a recovery.
- Numbers are clamped (rotate ±360°, wait 0–10 s). Temperature is 0 so runs are repeatable.
- The raw model output is always displayed, so the audience can compare what the model said with what the robot did.

### 5.5 How to measure accuracy
For each demo run, record from the on-screen log and the bridge console:

| Metric | Target |
|---|---|
| Task success (final state matches the command) | All core commands succeed |
| Valid JSON on first try | ≥ 95 % of turns |
| Hallucinated ids (rejected by validation) | 0 in the final recorded demo |
| Steps used vs. the minimal plan in section 6 | Within +2 steps for simple tasks |
| Correct ask-back on the ambiguous command | Asks once, then acts on the answer |
| Correct refusal on the impossible command | `impossible` after a bounded search, no fake success |
| Recoveries | Every failure followed by a different, sensible action |

Report any deviations honestly in the write-up, together with the model used and the feedback mode.

---

## 6. Demonstration scenarios and expected behaviour

Run these in order, from simple to hard. The expected plans are the minimal correct behaviour; small variations
(an extra `look_around`, naming a specific shelf) are acceptable if the final state is correct.

1. **`put the basketball on the ball rack`** — basic multi-step plan.
   `move_to basketball → pick basketball → move_to ball_rack → place ball_rack → done`.
   Pass: the basketball sits on a free `ball_rack` shelf.

2. **`hand me the red dumbbell`**, then answer **`the large one`** — ambiguity handling.
   First turn: `status: "ask"`, e.g. "Do you want the small or the large red dumbbell?", empty plan.
   After the answer: `move_to large_red_dumbbell → pick large_red_dumbbell → done` (there is no separate person in
   the scene, so handing over means the robot ends holding it). Pass: one question, the correct dumbbell in hand.

3. **`put the blue water bottle in the trophy cabinet`** — enclosure handling.
   `move_to blue_water_bottle → pick blue_water_bottle → move_to trophy_cabinet → open trophy_cabinet →
   place trophy_cabinet → close trophy_cabinet → done`. Pass: the blue (not red) bottle is inside, door closed.

4. **`put all three tennis balls on the top shelf of the ball rack`** — perception-driven planning.
   The tennis balls are not known at the start: `explore ball_cart` (look up close) until `tennis_ball_1..3` appear,
   then three rounds of `move_to/pick tennis_ball_N → move_to ball_rack → place ball_rack_top`. Pass: all three on
   `ball_rack_top`; no tennis ball id is used before it is known.

5. **`bring me the swimming goggles`** — honest failure.
   Explore the likely places (locker rooms), find nothing matching, then `status: "impossible"` with a reason.
   Pass: no invented id, bounded search, clear explanation.

6. **`get the gold medal and put it in the trophy cabinet`** — the hardest, long-horizon task.
   Explore the locker rooms (open a door, walk in, look around, open lockers). Discover `locker_5` is locked and
   needs `locker_key`; keep exploring until `locker_key` is found in `locker_2`; `pick locker_key →
   move_to locker_5 → open locker_5` (the key stays in the lock, freeing the hand) `→ pick gold_medal →
   move_to trophy_cabinet → open trophy_cabinet → place trophy_cabinet → close trophy_cabinet → done`.
   Pass: the medal ends in the cabinet within 25 actions, with every locked-door error answered by fetching the key.

7. **`stop`** while any task is running — cancellation after the current step.

---

## 7. Workspace map

| Path | Role |
|---|---|
| `vlm_bridge.py` | System prompt, Ollama and OpenRouter backends, JSON extraction and validation, the step loop with its limits, the WebSocket server, `--selftest` and `--test` |
| `vlm.properties` | Backend switch (`openrouter` or `ollama`), model names, fallback models, feedback mode, retries |
| `SportsRobot/Scripts/CommandClient.cs` | Unity chat UI, camera capture (JPEG), observations to the bridge, action dispatch, raw JSON display |
| `SportsRobot/Scripts/RobotAgent.cs` | Executes each action; explore, look_around, head aiming |
| `SportsRobot/Scripts/Perception.cs` | Seen / visited / checked-up-close bookkeeping with raycast and pixel-size tests |
| `SportsRobot/Scripts/Interactable.cs` | Every object the VLM can name; shelves, slots, lockers, keys, doors, state descriptions |
| `SportsRobot/Scripts/RoomArea.cs` | Rooms and their doors |
| `SportsRobot/Editor/SportsRoomBuilder.cs` | One-click scene generator and NavMesh bake (menu **Sports Robot ▸ Scene Builder**) |
| `SportsRobot/Layouts/sports_room.json` | The room design described in section 2 |
| `tools/preview_layout.py`, `previews/` | Offline layout renders and real Unity screenshots used for `--test` |

### Models
- **Self-hosted:** Ollama with `qwen3-vl:8b-instruct` (or `qwen3-vl:4b-instruct` on smaller machines).
- **Cloud:** OpenRouter free tier. No free Qwen vision model exists there, so the default is
  `google/gemma-4-31b-it:free` with fallbacks `google/gemma-4-26b-a4b-it:free` and `nvidia/nemotron-3.5-super-vl:free`.
  The console prints which model actually answered. A free key allows 50 requests per day.

### Running
```bash
python3 -m pip install websockets numpy matplotlib
python3 vlm_bridge.py --selftest
python3 vlm_bridge.py --test "put the basketball on the ball rack" \
        --image previews/unity_eye_start.png --overhead previews/unity_overhead.png
python3 vlm_bridge.py            # then press Play in Unity (Unity 6 LTS, Universal 3D, AI Navigation, TMP Essentials)
```

---

## 8. Constraints for anyone changing this project

- Keep action names and message fields identical in `vlm_bridge.py`, `CommandClient.cs` and `RobotAgent.cs`.
- Keep layout ids unique, descriptive and `snake_case`; rebuild the scene after editing the layout and regenerate previews.
- Extend `selftest()` for any change to validation or the feedback loop, and run `--selftest` before submitting.
- **Never weaken the allowed-action and known-object validation** — it is what makes the demonstration trustworthy.
- Never hardcode, log or commit the OpenRouter key. It is read at runtime from `OPENROUTER_API_KEY` or the
  git-ignored `creds/openRouter.txt`; leave `creds/` out of any submission archive.
- Python and C# use four-space indentation; layout JSON uses two spaces.
