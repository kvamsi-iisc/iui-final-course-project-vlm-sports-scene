# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`AGENTS.md` holds the repo's style, testing, and security rules; `README.md` covers setup, demo commands, and Unity steps; `PROJECT_PROMPT.md` is the full spec (room layout, VLM contract, accuracy requirements). Read these before larger changes.

## Commands

Use the project venv (`.venv/`, Python 3.9) from the repo root:

```bash
.venv/bin/python -m pip install -r requirements.txt   # websockets, Pillow
.venv/bin/python vlm_bridge.py --selftest              # offline tests: no model, no Unity
.venv/bin/python tools/run_local.py                    # start the right Ollama runtime + bridge on localhost:8765
.venv/bin/python tools/run_local.py --model llama --test "put the basketball on the ball rack" \
    --image previews/unity_eye_start.png --overhead previews/unity_overhead.png   # one-shot plan, no Unity
python3 tools/preview_layout.py SportsRobot/Layouts/sports_room.json previews     # needs numpy, matplotlib
```

- There is no test framework: `--selftest` runs every assertion in `selftest()` and `selftest_backends()` in `vlm_bridge.py`. You can't run one scenario alone. Add new scenarios there.
- `vlm_bridge.py` flags (`--backend`, `--model`, `--feedback every_step|after_plan`, `--config`) override `vlm.properties` for one run. `--test` with `--known`/`--holding` simulates a single Unity observation.
- Unity code can't be built or run from here. It's an asset bundle that gets copied into a Unity 6 project's `Assets/`, and the scene is regenerated with **Sports Robot > Scene Builder > Build scene**.

## Architecture

The system is a closed loop between Unity and a Python bridge. The bridge sends **one action at a time**:

1. `CommandClient.cs` sends an observation over WebSocket: `type` (`command` | `answer` | `result` | `cancel`), `text`, eye/overhead images as base64, `known` ids (only objects actually seen), `states`, `holding`, `location`, and for results `ok`/`error`/`note`/`newly_seen`.
2. `Brain.handle()` in `vlm_bridge.py` keeps a per-connection `Task` (history, answers, remaining plan, step and failure counts). It replies `{kind: act|ask|done|fail, reply, raw, error, step, maxSteps, failures, maxFailures, next?}`.
3. `Brain.decide()` either pops the next pre-planned step (in `after_plan` mode, after a successful result) or calls the VLM with `SYSTEM_PROMPT` + `build_prompt()`. The reply goes through `parse_reply()`/`clean_step()`, which reject unknown actions, unknown ids, and bad parameters. A rejected reply counts as a failure and the model is asked again. Limits: `MAX_STEPS = 25` and `MAX_FAILURES = 5`.
4. `RobotAgent.cs` executes the action (NavMesh walking, parenting to the hand, door hinges, explore/look_around search behaviour) and reports back. `Perception.cs` decides what counts as "seen" (in view, not occluded, at least 4 px), and that is the only source of `known` object ids.

The action vocabulary is defined in three places that must stay in sync: `ACTIONS` + `SYSTEM_PROMPT` in `vlm_bridge.py`, `RobotAction`/`BridgeMessage` in `CommandClient.cs`, and the action switch in `RobotAgent.cs`.

Backends (`make_backend()`): `OllamaBackend` (default, local) and `OpenRouterBackend` (opt-in only; it never acts as an automatic fallback from Ollama). The Qwen model sends the two camera images separately, while Llama gets them merged into one labeled image (`combine_camera_images`). Model aliases (`qwen`, `llama`) and per-model overrides such as `ollama.llama.num_ctx` live in `vlm.properties`.

`tools/run_local.py` picks the Ollama binary for the selected model, because the two models need different runtimes: Qwen uses `.local/ollama/` (0.40.16), and Llama uses `.local/ollama-0.24.0/`. Both share `.local/ollama-models/`. The launcher binds `127.0.0.1:11434` and only stops processes it started itself.

Scene: `SportsRobot/Layouts/sports_room.json` is the source of truth. Its `_readme` block documents the schema: rooms, doors, furniture kinds, shelf ids `<id>_bottom|_middle|_top`, locked lockers with keys, and roofed rooms that the overhead camera can't see into. `SportsRoomBuilder.cs` builds the Unity scene from it (`SportsRoomDecor.cs` is the same `partial class SceneGen`: textures, court lines, signs, lighting, robot model), and `tools/preview_layout.py` reproduces the same geometry without Unity. Change both whenever geometry rules change.

Layers carry meaning: 1 = hidden from the audience camera only (ceilings, the south wall, rack backs), 2 = see-through for perception rays, 4 = robot body (hidden from its own eye), 5 = audience-only overlays drawn by `RobotVisuals.cs` (hidden from both VLM cameras). Post-processing is applied to the audience camera only, by reflection, so VLM images stay raw.

## Notes

- `queries.txt` and `.local/*.log`/`*.json` are records of past design decisions and smoke tests, not inputs.
- Keep `.local/`, `.venv/`, and `creds/` out of archives and commits.
