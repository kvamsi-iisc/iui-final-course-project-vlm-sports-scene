# Repository Guidelines

## Project Structure & Module Organization
- `vlm_bridge.py` handles model calls, plan validation, and the WebSocket feedback loop.
- `SportsRobot/Scripts/` contains Unity runtime components for commands, robot actions, perception, objects, and rooms.
- `SportsRobot/Editor/SportsRoomBuilder.cs` generates the scene from `SportsRobot/Layouts/sports_room.json`.
- `tools/preview_layout.py` renders layout previews into `previews/`.
- `vlm.properties` configures the model backend and feedback behavior. This is a Unity asset bundle; copy `SportsRobot/` into a separate Unity project's `Assets/` directory.

## Build, Test, and Development Commands
Run Python commands from the repository root:

```bash
python3 -m pip install websockets numpy matplotlib
python3 vlm_bridge.py --selftest
python3 vlm_bridge.py
python3 tools/preview_layout.py SportsRobot/Layouts/sports_room.json previews
```

These install bridge/preview dependencies, run offline checks, start the bridge at `localhost:8765`, and regenerate previews, respectively.

Use Unity 6 LTS with the Universal 3D template, AI Navigation, and TMP Essentials. Select **Sports Robot > Scene Builder > Build scene** to generate `Assets/SportsRobot/Scenes/SportsRoom.unity` and bake navigation. Start the bridge, then enter Play mode. Rebuild after layout edits.

## Coding Style & Naming Conventions
Use four-space indentation in Python and C#, and two-space indentation in layout JSON. Follow existing Python `snake_case` functions and `UPPER_CASE` constants. Use PascalCase C# classes/methods, camelCase fields, and matching component filenames. Keep layout IDs unique, descriptive, and snake_case, such as `locker_room_a`.

No formatter or linter configuration is present. Match surrounding code. Keep action names and message fields consistent between the Python bridge and Unity client/executor; keep preview geometry aligned with the scene builder.

## Testing Guidelines
Tests are assertion-based scenarios in `vlm_bridge.py:selftest()`; no separate test framework or coverage threshold is configured. Extend these scenarios for validation or feedback-loop changes and run `--selftest` before submitting.

For Unity changes, rebuild and manually exercise picking/placing, exploration, clarification, and cancellation using README demo commands. Inspect regenerated previews for layout changes.

## Commit & Pull Request Guidelines
Git history is unavailable in this checkout. Use concise, imperative commit subjects, such as `Fix locker exploration recovery`. PRs should describe behavior changes, link relevant issues, list verification performed, and include screenshots for scene/UI changes.

## Security & Configuration
Keep keys in `OPENROUTER_API_KEY` or ignored `creds/openRouter.txt`. Never commit credentials, print them in logs, or include `creds/` in submission archives. Preserve allowed-action and known-object validation of model output.
