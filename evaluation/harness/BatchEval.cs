using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

// Drives one typed command through the real CommandClient in Play mode, answers the robot's questions with a
// fixed keyword policy (the same for every model), and writes the final world state to a JSON file.
public static class BatchEval
{
    [Serializable] class Thing { public string id, state, containedIn; public bool open, locked; }
    [Serializable] class QA { public string question, answer; }
    [Serializable] class Report
    {
        public string command, outcome, holding;
        public float seconds;
        public List<QA> questions = new List<QA>();
        public List<Thing> things = new List<Thing>();
        public List<string> log;
    }

    const BindingFlags Any = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public;
    static string Env(string k, string d = "") => Environment.GetEnvironmentVariable(k) ?? d;
    static bool sent;
    static double start, answerAt;
    static readonly Report report = new Report();

    public static void Run()
    {
        EditorSceneManager.OpenScene("Assets/SportsRobot/Scenes/SportsRoom.unity");
        UnityEngine.Object.FindFirstObjectByType<CommandClient>().serverUrl = Env("EVAL_URL", "ws://localhost:8766");
        EditorApplication.update += Tick;
        EditorApplication.EnterPlaymode();
    }

    static void Tick()
    {
        if (!EditorApplication.isPlaying) return;
        var client = UnityEngine.Object.FindFirstObjectByType<CommandClient>();
        if (!client) return;
        double now = EditorApplication.timeSinceStartup;
        // Batch mode has no Game window, so the two cameras the model looks through are rendered by hand.
        if (client.robotCamera) client.robotCamera.Render();
        if (client.overheadCamera) client.overheadCamera.Render();
        string state = typeof(CommandClient).GetField("state", Any).GetValue(client).ToString();
        var lines = (List<string>)typeof(CommandClient).GetField("lines", Any).GetValue(client);

        if (!sent)
        {
            if (Time.time < 2f || client.statusText.text != "Ready") { if (Time.time > 30f) Finish(client, lines, "no_bridge"); return; }
            sent = true;
            start = now;
            report.command = Env("EVAL_CMD");
            Submit(client, report.command);
            return;
        }
        if (now - start > float.Parse(Env("EVAL_TIMEOUT", "1200"))) { Finish(client, lines, "timeout"); return; }

        if (state == "AwaitingAnswer")
        {
            if (answerAt == 0) { answerAt = now + 1.0; return; }
            if (now < answerAt) return;
            answerAt = 0;
            string q = lines.LastOrDefault(l => l.StartsWith("<b>Robot:</b>")) ?? "";
            q = q.Replace("<b>Robot:</b> ", "").Replace("<noparse>", "").Replace("</noparse>", "");
            if (report.questions.Count >= 5) { Finish(client, lines, "too_many_questions"); return; }
            string a = Answer(q);
            report.questions.Add(new QA { question = q, answer = a });
            Submit(client, a);
        }
        else if (state == "Idle")
            Finish(client, lines, lines.Any(l => l.Contains("Done.</color>")) ? "done" : "stopped");
    }

    // Rules are "keyword|keyword=>answer" separated by ";;"; the first rule with a matching keyword wins.
    static string Answer(string question)
    {
        string q = question.ToLowerInvariant();
        foreach (var rule in Env("EVAL_ANSWERS").Split(new[] { ";;" }, StringSplitOptions.RemoveEmptyEntries))
        {
            var parts = rule.Split(new[] { "=>" }, StringSplitOptions.None);
            if (parts.Length == 2 && parts[0].Split('|').Any(k => q.Contains(k.Trim().ToLowerInvariant()))) return parts[1].Trim();
        }
        return "Use your best judgement.";
    }

    static void Submit(CommandClient client, string text) =>
        typeof(CommandClient).GetMethod("OnSubmit", Any).Invoke(client, new object[] { text });

    static void Finish(CommandClient client, List<string> lines, string outcome)
    {
        EditorApplication.update -= Tick;
        report.outcome = outcome;
        report.seconds = (float)(EditorApplication.timeSinceStartup - start);
        report.holding = client.robot.HeldName;
        report.log = lines;
        foreach (var i in Interactable.All.OrderBy(i => i.id))
            report.things.Add(new Thing { id = i.id, state = i.Describe(), containedIn = i.ContainedIn ? i.ContainedIn.id : "", open = i.IsOpen, locked = i.locked });
        File.WriteAllText(Env("EVAL_OUT"), JsonUtility.ToJson(report, true));
        Debug.Log("EVAL-DONE " + outcome);
        EditorApplication.Exit(0);
    }
}
