# Model evaluation: Qwen3-VL 8B vs Llama 3.2 Vision 11B

Both models were given the same typed commands in the same Unity scene, through the same bridge code and prompt,
and every run was traced end to end: each model call (latency and raw reply), each robot action and its result,
each question asked, and the final state of the world.

**Result in one line:** Qwen3-VL 8B completed 6 of 8 tasks at about 25 s per model call with no malformed replies;
Llama 3.2 Vision 11B completed 2 of 7 at about 50 s per call, with a third of its replies rejected by the bridge.

## 1. Setup

| | |
|---|---|
| Machine | Apple M2 Pro, 16 GB, models served locally by Ollama |
| Models | `qwen3-vl:8b-instruct` (Ollama 0.40.16) and `llama3.2-vision:11b` (Ollama 0.24.0) |
| Feedback mode | `after_plan` (the setting in `vlm.properties`): the model is called again when a plan finishes or a step fails |
| Scene | The generated Sports Complex, started fresh for every scenario, run in Unity Play mode without a window |
| Images | Qwen receives the eye and overhead images separately; Llama receives them merged into one labelled image (project default) |
| Context | Qwen 8192 tokens, Llama 4096 tokens (project defaults) |
| User answers | A fixed keyword-based responder, identical for both models (see section 6) |
| Code | Both models were measured on the same frozen code, after the fixes in section 5 |
| Runs | One run per scenario per model |

Llama's eighth scenario was not run: the Llama suite was stopped after seven scenarios (about an hour).

## 2. Headline comparison

Figures are for the seven scenarios both models ran, unless marked.

| KPI | Qwen3-VL 8B | Llama 3.2 Vision 11B | Better |
|---|---|---|---|
| Tasks completed correctly | **6 of 7** (6 of 8 overall) | 2 of 7 | Qwen |
| Response time per model call, mean | **25.4 s** | 50.3 s | Qwen |
| Response time, median / 90th percentile / slowest | **28.2 / 31.9 / 38.9 s** | 44.5 / 79.8 / 156.1 s | Qwen |
| Model calls needed for the 7 tasks | **27** | 67 | Qwen |
| Total time for the 7 tasks | **14.4 min** | 58.7 min | Qwen |
| Replies accepted on the first try | **100 %** (27 of 27) | 67 % (45 of 67) | Qwen |
| Robot actions that failed | **1 of 57** | 7 of 39 | Qwen |
| Questions the model chose to ask | **2** | 7 | Qwen |
| Times the bridge had to step in and ask the user ("I'm stuck") | **0** | 8 | Qwen |
| Model-server errors | **0** | 1 (GPU out of memory) | Qwen |
| Claimed success when the task was not done | 1 of 7 (2 of 8 overall) | **0** | Llama |

A reply is "accepted on the first try" when it is valid JSON, uses only allowed actions and known object ids, and
does not repeat a step that just succeeded or a question already answered.

## 3. Per-scenario results

Time is the whole task, from typing the command to the robot stopping. "Calls" are model calls; "min." is the
smallest number of robot actions that solves the task.

| # | Command | Qwen3-VL 8B | Llama 3.2 Vision 11B |
|---|---|---|---|
| 1 | pick up the soccer ball | **Pass**, 70 s, 2 calls, 2 actions (min. 2) | **Pass**, 277 s, 7 calls, 4 actions. Picked it up, then drove to the ball rack and looped until the bridge stepped in |
| 2 | put the basketball on the ball rack | **Pass**, 52 s, 2 calls, 4 actions (min. 4) | **Pass**, 354 s, 9 calls, 5 actions. Placed it, then kept trying to place it again |
| 3 | hand me the red dumbbell (two exist) | **Pass**, 96 s, 4 calls. Asked "small or large?", then fetched the large one | **Fail**, 369 s, 7 calls. Asked the right question, then searched a locker room and the ball cart and tried to pick up the rack itself |
| 4 | put the blue water bottle in the trophy cabinet | **Pass**, 63 s, 2 calls, 5 actions (min. 6). Left the cabinet door open | **Fail**, 801 s, 10 calls. Asked four times for a key the cabinet does not need; the run ended when Ollama ran out of GPU memory |
| 5 | put all three tennis balls on the top shelf of the ball rack | **Pass**, 103 s, 3 calls, 13 actions (min. 13) | **Fail**, 577 s, 12 calls. 0 of 3 on the top shelf; two ended on the bottom shelf |
| 6 | bring me the swimming goggles (none exist) | **Pass**, 239 s, 8 calls. Searched both locker rooms, fetched the key, opened the locked locker, then said it could not be done | **Fail**, 735 s, 14 calls. Re-checked the same two lockers, never entered the second room, never said it could not be done |
| 7 | get the gold medal and put it in the trophy cabinet | **Fail**, 239 s, 6 calls. Found the key and opened the locked locker, then put the gold *trophy* in the cabinet and reported success | **Fail**, 411 s, 8 calls. Kept using an invented id (`lockers`), which the bridge rejected; only one action ran |
| 8 | put the blue dumbbell in locker 5 (locked, in the other room) | **Fail**, 150 s, 5 calls. Put it in locker 3 and reported "placed in locker 5" | not run |

## 4. Quality ratings

These are a reviewer's ratings from reading every transcript (1 = poor, 5 = excellent), not automatic measurements.
The transcripts are in `evaluation/traces/` so the ratings can be checked.

| Quality | Qwen3-VL 8B | Llama 3.2 Vision 11B | Evidence |
|---|---|---|---|
| Action plan | **4** | 2 | Qwen plans the whole task in one reply and hit the minimal action count on tasks 1, 2 and 5. Llama plans one step per reply, so every step costs a 40–50 s call |
| Tracking the robot's state | **4** | 1 | Llama repeatedly said "I'm holding the basketball" after placing it and tried to pick up furniture (`pick dumbbell_rack`, `pick ball_rack`) |
| Questions | **4** | 2 | Both asked the correct "small or large?" question. Qwen asked one unnecessary permission question (it already knew where the key was). Llama invented a key for an unlocked cabinet and asked about it four times |
| Behaviour when blocked | **4** | 1 | Qwen: locked locker → fetched the key → opened it → continued. Llama never recovered on its own; the bridge's stuck-detection ended its loops 8 times |
| Honest final answer | 3 | 3 | Qwen refused the impossible task correctly but twice reported success after doing something else (tasks 7 and 8). Llama made no false success claim but also never gave the honest "impossible" on task 6 |
| Speed | **4** | 1 | 25 s against 50 s per call, and Llama needs 2.5 times as many calls |

## 5. What the testing found and fixed

The first test pass exposed five defects. All were fixed before the final measurement, so the tables above reflect the fixed code.

| Defect | Seen as | Fix |
|---|---|---|
| Door approached from the wrong side | From inside one locker room, `explore` of the other room failed forever with "no path" | `RobotAgent.cs` picks the side of a door the robot can actually walk to |
| Key never noticed | The robot opened a locker while standing at the neighbouring one, whose open door hid the inside | `RobotAgent.cs` steps straight in front of a locker or cabinet before opening or inspecting it |
| Same failed step proposed again | Three identical failures in a row, each a slow model call | The bridge asks the user instead of re-running a step that just failed, and after 3 failures in a row |
| Same question asked again | Qwen asked "Should I explore locker_room_b?" five times after being told yes | The bridge rejects a question that was already answered and tells the model to act on the answer |
| Successful step repeated for no reason | Llama repeated `move_to ball_rack` for 30 minutes | The bridge rejects a `move_to`/`open`/`close` that just succeeded and asks for the next step |

A missing key is now asked about immediately, without a model call. Prompt rules were added for: never substituting
another object, exploring both locker rooms for an unknown named place, not asking permission to explore, and
closing a cabinet after use.

For reference, Qwen's first pass (before the last three fixes) also scored 6 of 8, with 94 % of replies accepted
on the first try against 100 % in the final run.

## 6. Limits of this evaluation

- **One run per scenario.** Temperature is 0, but the runs are not perfectly repeatable; pass rates from a single
  run each are indicative, not statistically firm.
- **Memory.** Llama needs about 11 GB on a 16 GB machine. Free memory was 70 % when the Qwen suite started and
  17 % when the Llama suite started. Part of Llama's slowness, and its one out-of-memory failure (task 4), come from this
  hardware limit rather than from the model's reasoning. A machine with more memory would give Llama better response times.
- **Scripted user.** Questions were answered by fixed keyword rules. On task 4 Llama asked about a key the cabinet
  does not need, and the rule for "key" answered "search the lockers", which a real person would not have said.
  That answer may have reinforced Llama's mistake.
- **Different input handling.** Llama receives one merged image and half the context length (project defaults for
  a 16 GB machine), so the comparison is of each model as configured in this project, not of the models in isolation.
- **Task 5 is easier than the specification intended.** In the current scene the tennis balls are visible from
  the overhead camera at the start, so no exploration was needed.
- **Llama's task 8 was not run**, and the "better" column for honesty rests on few cases.

## 7. Remaining weaknesses and next steps

1. **Qwen substitutes objects it cannot yet name.** On task 7 it planned ahead before the medal had been seen, so it
   could not use the id `gold_medal` and picked `gold_trophy` instead. Re-planning as soon as something new is seen
   (or `feedback = every_step`) would let it name the medal once the locker is open.
2. **Qwen reports success without checking the goal.** On task 8 it never explored the second locker room and put
   the dumbbell in locker 3. A final check in the bridge (does the state line of the named object match the request?)
   would catch both false successes.
3. **Llama 3.2 Vision 11B is not practical for this project on 16 GB.** It is slower, needs one call per step, and
   loses track of what it holds. Qwen3-VL 8B should stay the default.

## 8. Reproducing

`evaluation/harness/` holds the test rig used: `eval_bridge.py` (the project's `Brain` and backend with timing),
`BatchEval.cs` (types the command in Unity Play mode, answers questions, writes the final world state),
`run_eval.py` (runs the scenarios) and `analyze.py` (produces `evaluation/results.json`). The paths inside them point
at the scratch Unity project used for this run and need adjusting before re-use. `evaluation/traces/` contains one
`.jsonl` trace and one final-state `.json` per run.

## 9. Follow-up: Qwen after the "done" verification fixes

After the comparison above, the weaknesses listed in section 7 were addressed for Qwen and its eight scenarios
were run again. Llama was not re-run. Traces are in `evaluation/traces_after_fixes/`.

**What was added**

| Change | Effect |
|---|---|
| The bridge checks the state lines before accepting "done" | "put X in Y" needs X's state to say it is in or on Y; "pick up X" needs the robot to be holding X |
| A named, numbered thing that has not been seen blocks "done" | "locker 5" can no longer be swapped for locker 3 |
| The object put away must be the kind of thing asked for | Putting the gold trophy away for "the gold medal" is refused and the model is sent to find the medal |
| A cabinet opened during the task must be closed before "done" | The bottle task now ends with the cabinet closed |
| Plain permission questions ("Should I open it?") are answered yes for the user | Fewer needless interruptions |
| A place with an empty hand tells the model where the task's object is | Helps after the robot set something down to free its hand |
| A locker the robot has looked into counts as checked | `explore` moves on to the next locker instead of revisiting one |
| After three wrong "done" claims the robot reports failure | A wrong success can no longer be the final answer for these cases |

**Result**

| | Before (section 2, all 8 tasks) | After the fixes |
|---|---|---|
| Tasks completed correctly | 6 of 8 | **7 of 8** |
| False "done" reports | 2 | **0** |
| Model calls for the 8 tasks | 32 | 48 |
| Total time for the 8 tasks | 16.9 min | 29.3 min |
| Robot actions that failed | 1 of 66 | 4 of 86 |

| Task | Before | After |
|---|---|---|
| 1–3, 5 | Pass | Pass (within a few seconds of the earlier times) |
| 4 bottle in the trophy cabinet | Pass, cabinet left open (63 s) | Pass, cabinet closed (98 s) |
| 6 swimming goggles | Pass | Pass |
| 7 gold medal | False "done" with the gold trophy | **Pass.** The trophy was refused, Qwen fetched the real medal and closed the cabinet (394 s, 11 calls). The trophy stays in the cabinet as a side effect |
| 8 blue dumbbell in locker 5 | False "done", dumbbell in locker 3 | Honest failure. Qwen found locker 5, fetched the key, opened the locker, then ran out of the 25-action limit while going back for the dumbbell it had set down |

The extra calls and time come mainly from tasks 7 and 8 now being carried through instead of ending early with a
wrong success. Response time per call was 30.7 s on average in this run against 25.5 s before; the machine had
less free memory (35 % against 70 % at the start), so the two latency figures are not a like-for-like comparison.

**Still open**

- Task 8 does not complete. It needs about 25 actions the way Qwen does it (it searches the wrong locker room first
  and carries the dumbbell the whole way), which is the action limit.
- If the model says "done" before the object named in the task has ever been seen, and nothing was put away, the
  bridge has nothing to check against.
- One run per scenario; Qwen's behaviour on task 8 differed across four attempts.
- Tasks 1–5 and 7 were run just before two last small bridge changes (guard nudges no longer count towards the
  problem limit; the empty-hand hint). Those changes only act after an empty-hand place or near the problem limit,
  which none of those six runs reached; task 6 and task 8 were run after them.
