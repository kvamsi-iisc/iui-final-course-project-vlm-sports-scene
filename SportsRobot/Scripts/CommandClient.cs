// CommandClient.cs — Unity side of the pipeline.
// Sends the user's command plus what the robot perceives right now (eye camera, overhead camera, the
// objects it has seen and their state) to the Python bridge, runs the single action that comes back,
// reports the result with fresh images, and repeats until the bridge says the task is done, asks the
// user a question, or gives up.
using System;
using System.Collections;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

[Serializable] public class RobotAction { public string action; public string target; public float degrees; public float seconds; }

[Serializable] public class BridgeMessage
{
    public string kind;                 // act | ask | done | fail
    public string reply, raw, error;
    public RobotAction next;            // the one action to run now (kind == act)
    public int step, maxSteps, failures, maxFailures;
}

[Serializable] class Observation
{
    public string type;                 // command | answer | result | cancel
    public string text;                 // what the user typed (command / answer)
    public bool ok;                     // result of the last action
    public string error, note;
    public string image_b64, overhead_b64;
    public string[] known, states, newly_seen, unexplored;
    public string holding, location;
}

public class CommandClient : MonoBehaviour
{
    [Header("Connection")]
    public string serverUrl = "ws://localhost:8765";

    [Header("Scene")]
    public RobotAgent robot;
    public Perception perception;
    public RobotVisuals visuals;          // status lights, path line, target highlight (optional)
    public Camera robotCamera;            // what the VLM sees through the robot's eyes
    public Camera overheadCamera;         // bird's-eye view of the open (unroofed) rooms
    public int imageWidth = 640, imageHeight = 480;

    [Header("UI")]
    public TMP_InputField inputField;     // user types commands here
    public TMP_Text logText;              // shows VLM output + execution log
    public TMP_Text statusText;
    public ScrollRect logScroll;
    public RawImage eyeView, overheadView;
    public int maxLogLines = 250;

    enum State { Idle, Thinking, Acting, AwaitingAnswer }

    ClientWebSocket ws;
    readonly CancellationTokenSource cts = new CancellationTokenSource();
    readonly ConcurrentQueue<string> inbox = new ConcurrentQueue<string>();
    readonly List<string> lines = new List<string>();
    RenderTexture eyeRT, topRT;
    State state;
    bool stopRequested, scrollPending;

    async void Start()
    {
        if (inputField) inputField.onSubmit.AddListener(OnSubmit);
        if (!perception && robot) perception = robot.GetComponent<Perception>();
        if (!visuals && robot) visuals = robot.GetComponent<RobotVisuals>();
        if (perception) perception.imageHeight = imageHeight;
        eyeRT = LiveView(robotCamera, eyeView);
        topRT = LiveView(overheadCamera, overheadView);
        SetStatus("Connecting");
        await Connect();
    }

    RenderTexture LiveView(Camera cam, RawImage view)
    {
        if (!cam) return null;
        var rt = new RenderTexture(imageWidth, imageHeight, 24);
        cam.targetTexture = rt;
        cam.enabled = true;
        if (view) view.texture = rt;
        return rt;
    }

    async Task Connect()
    {
        ws?.Dispose();
        ws = new ClientWebSocket();
        try
        {
            await ws.ConnectAsync(new Uri(serverUrl), cts.Token);
            Log("<color=green>Connected to VLM bridge</color>");
            SetStatus("Ready");
            _ = ReceiveLoop();
        }
        catch (Exception e)
        {
            Log("<color=red>Connect failed:</color> " + e.Message + " (is vlm_bridge.py running?)");
            SetStatus("Not connected");
        }
    }

    async Task ReceiveLoop()
    {
        var buffer = new byte[1 << 16];
        try
        {
            while (ws.State == WebSocketState.Open)
            {
                using var ms = new MemoryStream();
                WebSocketReceiveResult r;
                do
                {
                    r = await ws.ReceiveAsync(new ArraySegment<byte>(buffer), cts.Token);
                    if (r.MessageType == WebSocketMessageType.Close) return;
                    ms.Write(buffer, 0, r.Count);
                } while (!r.EndOfMessage);
                inbox.Enqueue(Encoding.UTF8.GetString(ms.ToArray()));
            }
        }
        catch (Exception e) when (!cts.IsCancellationRequested) { Log("<color=red>Connection lost:</color> " + e.Message); }
    }

    void Update()
    {
        while (inbox.TryDequeue(out var json)) Handle(JsonUtility.FromJson<BridgeMessage>(json));
    }

    void LateUpdate()
    {
        if (!scrollPending || !logScroll) return;
        scrollPending = false;
        Canvas.ForceUpdateCanvases();
        logScroll.verticalNormalizedPosition = 0f;
    }

    void Handle(BridgeMessage m)
    {
        if (!string.IsNullOrEmpty(m.raw)) Log($"<color=#8cf>VLM raw:</color> <noparse>{m.raw}</noparse>");
        if (!string.IsNullOrEmpty(m.error)) Log($"<color=orange>Problem:</color> <noparse>{m.error}</noparse>");
        if (!string.IsNullOrEmpty(m.reply)) Log($"<b>Robot:</b> <noparse>{m.reply}</noparse>");
        string counters = m.maxSteps > 0 ? $"step {m.step}/{m.maxSteps} | recoveries {m.failures}/{m.maxFailures}" : "";

        switch (m.kind)
        {
            case "act":
                if (stopRequested) { Stop(); return; }
                state = State.Acting;
                SetStatus($"Acting: {Show(m.next)} | {counters}");
                Visual(RobotVisuals.Mood.Acting, Show(m.next), m.next == null || string.IsNullOrEmpty(m.next.target) ? null : Interactable.Find(m.next.target));
                StartCoroutine(Act(m.next));
                break;
            case "ask":
                state = State.AwaitingAnswer;
                stopRequested = false;
                Log("<color=yellow>Waiting for your answer...</color>");
                Visual(RobotVisuals.Mood.Asking, "?");
                SetStatus("Waiting for your answer");
                break;
            default:
                state = State.Idle;
                stopRequested = false;
                Log(m.kind == "done" ? "<color=green>Done.</color>" : "<color=red>Stopped without finishing.</color>");
                Visual(m.kind == "done" ? RobotVisuals.Mood.Idle : RobotVisuals.Mood.Error, m.kind == "done" ? "" : "stopped");
                SetStatus($"Ready{(counters.Length > 0 ? " | last task: " + counters : "")}");
                break;
        }
    }

    IEnumerator Act(RobotAction a)
    {
        Log($"> {Show(a)}");
        string error = null, note = null;
        yield return robot.RunOne(a, (e, n) => { error = e; note = n; });
        if (error != null) Log($"<color=red>x {error}</color>");
        else if (!string.IsNullOrEmpty(note)) Log($"<color=#aaa>{note}</color>");
        yield return new WaitForEndOfFrame();   // let the cameras render the new state before capturing
        if (stopRequested) { Stop(); yield break; }
        state = State.Thinking;
        SetStatus("Thinking (looking at the result)");
        _ = Send("result", "", error == null, error ?? "", note ?? "");
    }

    void Stop()
    {
        stopRequested = false;
        state = State.Thinking;
        _ = Send("cancel", "", true, "", "");
    }

    void OnSubmit(string text)
    {
        if (string.IsNullOrWhiteSpace(text)) return;
        if (inputField) { inputField.text = ""; inputField.ActivateInputField(); }
        if (state == State.Thinking || state == State.Acting)
        {
            if (text.Trim().ToLowerInvariant() is "stop" or "cancel")
            {
                stopRequested = true;
                Log("Stopping after the current step.");
            }
            else Log("Still working on the last command. Type 'stop' to cancel it.");
            return;
        }
        Log($"\n<b>You:</b> <noparse>{text}</noparse>");
        string type = state == State.AwaitingAnswer ? "answer" : "command";
        state = State.Thinking;
        SetStatus("Thinking");
        _ = Send(type, text.Trim(), true, "", "");
    }

    async Task Send(string type, string text, bool ok, string error, string note)
    {
        if (ws == null || ws.State != WebSocketState.Open)
        {
            Log("Not connected, retrying...");
            await Connect();
            if (ws.State != WebSocketState.Open) { state = State.Idle; return; }
        }
        if (type != "cancel") Visual(RobotVisuals.Mood.Thinking, "thinking...");
        var obs = Observe(type, text, ok, error, note);
        var bytes = Encoding.UTF8.GetBytes(JsonUtility.ToJson(obs));
        try { await ws.SendAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, cts.Token); }
        catch (Exception e)
        {
            Log("<color=red>Send failed:</color> " + e.Message);
            state = State.Idle;
            SetStatus("Not connected");
            Visual(RobotVisuals.Mood.Error, "not connected");
        }
    }

    // ---------- what the robot perceives ----------

    Observation Observe(string type, string text, bool ok, string error, string note)
    {
        var known = (perception ? perception.Known : Interactable.All).OrderBy(i => i.id).ToList();
        Func<Interactable, bool> knows = perception ? perception.Knows : (Func<Interactable, bool>)(_ => true);
        Func<Interactable, bool> checkedClose = perception ? perception.Inspected : (Func<Interactable, bool>)(_ => true);
        var pos = robot.transform.position;
        var room = RoomArea.At(pos);
        string zone = FloorZone.Name(pos);
        var nearest = known.Where(i => i.kind != "room" && i != robot.Held)
                           .OrderBy(i => Flat(i.transform.position - pos)).FirstOrDefault();

        return new Observation
        {
            type = type,
            text = text,
            ok = ok,
            error = error,
            note = note,
            image_b64 = Capture(eyeRT),
            overhead_b64 = Capture(topRT),
            known = known.Select(i => i.id).ToArray(),
            states = known.Select(i => StateLine(i, knows, checkedClose, room)).ToArray(),
            newly_seen = perception ? perception.TakeNewlySeen().ToArray() : new string[0],
            unexplored = Unexplored().ToArray(),
            holding = robot.HeldName,
            location = $"in {(room ? room.Id : "a doorway")}{(zone.Length > 0 ? $" ({zone})" : "")}, facing {Compass(robot.transform.eulerAngles.y)}" +
                       (nearest ? $", {Flat(nearest.transform.position - pos):F1} m from {nearest.id}" : ""),
        };
    }

    string StateLine(Interactable i, Func<Interactable, bool> knows, Func<Interactable, bool> checkedClose, RoomArea here)
    {
        if (i.kind == "room" && i.TryGetComponent<RoomArea>(out var r))
        {
            string seen = perception && perception.Visited(r) ? "visited" : "not visited yet";
            return $"{i.id} (room{(r.roofed ? ", roofed, can't be seen from above" : "")}; {seen}" +
                   $"{(r == here ? "; robot is here" : "")}; doors: {string.Join(", ", r.doors.Select(d => d.id))})";
        }
        return i.Describe(knows, checkedClose, Where(i));
    }

    // Which part of the building a free-standing thing is in. Things on shelves or in lockers already say so.
    string Where(Interactable i)
    {
        if (i.partOf || i.kind == "door" || i == robot.Held) return null;
        var p = i.transform.position;
        string zone = FloorZone.Name(p);
        var room = RoomArea.At(p);
        string place = zone.Length > 0 ? zone : room ? room.Id : "";
        if (place.Length == 0) return null;
        return i.pickable ? $"on the floor, {place}" : $"in {place}";
    }

    static readonly string[] Headings = { "north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west" };
    static string Compass(float yaw) => Headings[Mathf.RoundToInt(Mathf.Repeat(yaw, 360f) / 45f) % 8];

    void Visual(RobotVisuals.Mood mood, string caption = "", Interactable workingOn = null)
    {
        if (visuals) visuals.Show(mood, caption, workingOn);
    }

    IEnumerable<string> Unexplored()
    {
        if (!perception) yield break;
        foreach (var r in perception.UnvisitedRooms)
        {
            var closed = r.doors.Where(d => !d.IsOpen).Select(d => d.id).ToList();
            yield return $"room {r.Id}" + (closed.Count == r.doors.Count && closed.Count > 0 ? $" (door {string.Join(", ", closed)} is closed)" : "");
        }
        foreach (var i in perception.Unchecked.OrderBy(i => i.id))
            yield return i.locked ? $"{i.id} (locked, needs {i.keyId})"
                 : i.openable && !i.IsOpen ? $"{i.id} (closed, never looked inside)"
                 : $"{i.id} (not checked up close)";
    }

    static float Flat(Vector3 d) { d.y = 0; return d.magnitude; }

    static string Show(RobotAction a)
    {
        if (a == null) return "";
        switch (a.action)
        {
            case "rotate": return $"rotate {a.degrees:0} deg";
            case "wait": return $"wait {a.seconds:0.#} s";
            default: return string.IsNullOrEmpty(a.target) ? a.action : $"{a.action} {a.target}";
        }
    }

    string Capture(RenderTexture rt)
    {
        if (!rt) return "";
        var prev = RenderTexture.active;
        RenderTexture.active = rt;
        var tex = new Texture2D(rt.width, rt.height, TextureFormat.RGB24, false);
        tex.ReadPixels(new Rect(0, 0, rt.width, rt.height), 0, 0);
        tex.Apply();
        RenderTexture.active = prev;
        var jpg = tex.EncodeToJPG(80);
        Destroy(tex);
        return Convert.ToBase64String(jpg);
    }

    // ---------- UI ----------

    void SetStatus(string s)
    {
        if (statusText) statusText.text = s;
    }

    void Log(string msg)
    {
        Debug.Log(msg);
        if (!logText) return;
        lines.Add(msg);
        if (lines.Count > maxLogLines) lines.RemoveRange(0, lines.Count - maxLogLines);
        logText.text = string.Join("\n", lines);
        scrollPending = true;
    }

    void OnDestroy()
    {
        cts.Cancel();
        ws?.Dispose();
        if (eyeRT) eyeRT.Release();
        if (topRT) topRT.Release();
    }
}
