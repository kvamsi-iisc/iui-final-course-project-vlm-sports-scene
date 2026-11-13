// SportsRoomBuilder.cs — editor tool that builds the whole scene from a layout JSON file.
// Menu: Sports Robot ▸ Scene Builder. Edit the layout file, click Build scene, press Play.
using System;
using System.Collections.Generic;
using System.Linq;
using TMPro;
using Unity.AI.Navigation;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.AI;
using UnityEngine.EventSystems;
using UnityEngine.Rendering;
using UnityEngine.UI;

[Serializable] public class LayoutFile
{
    public string name;
    public float wall_height;
    public string wall_color, wall_accent;
    public LayoutRoom[] rooms;
    public LayoutZone[] zones;
    public LayoutDoor[] doors;
    public LayoutThing[] furniture;
    public LayoutThing[] items;
    public LayoutRobot robot;
}
[Serializable] public class LayoutRoom { public string id, label, floor_color, floor; public float[] rect; public bool ceiling; }
[Serializable] public class LayoutZone { public string label, color, style, hoop, sign_faces; public float[] rect, sign_at; }
[Serializable] public class LayoutDoor { public string id, sign, color; public float[] at; public float width; public bool open; }
[Serializable] public class LayoutThing
{
    public string id, kind, style, color, color2, faces, on, label, key, model;
    public float[] at, size;
    public float scale;
    public int levels;
    public bool locked, open;
}
[Serializable] public class LayoutRobot { public float[] at; public string faces; }

public class SportsRoomBuilder : EditorWindow
{
    public const string Root = "Assets/SportsRobot";
    const string PrefLayout = "SportsRobot.layout", PrefRobot = "SportsRobot.robot";

    TextAsset layout;
    GameObject robotModel;

    [MenuItem("Sports Robot/Scene Builder")]
    static void Open() => GetWindow<SportsRoomBuilder>("Sports Room Builder");

    void OnEnable()
    {
        layout = AssetDatabase.LoadAssetAtPath<TextAsset>(EditorPrefs.GetString(PrefLayout, Root + "/Layouts/sports_room.json"));
        robotModel = AssetDatabase.LoadAssetAtPath<GameObject>(EditorPrefs.GetString(PrefRobot, ""));
    }

    void OnGUI()
    {
        EditorGUILayout.HelpBox("Edit the layout JSON, then click Build scene. The scene is regenerated from scratch " +
                                "every time, so make changes in the layout file rather than in the scene.", MessageType.Info);
        layout = (TextAsset)EditorGUILayout.ObjectField("Layout file", layout, typeof(TextAsset), false);
        robotModel = (GameObject)EditorGUILayout.ObjectField("Robot model (optional)", robotModel, typeof(GameObject), false);
        EditorGUILayout.Space();
        using (new EditorGUI.DisabledScope(layout == null))
        {
            if (GUILayout.Button("Build scene", GUILayout.Height(32)))
            {
                EditorPrefs.SetString(PrefLayout, AssetDatabase.GetAssetPath(layout));
                EditorPrefs.SetString(PrefRobot, robotModel ? AssetDatabase.GetAssetPath(robotModel) : "");
                Build(layout.text, robotModel);
            }
        }
    }

    static void Build(string json, GameObject robotModel)
    {
        if (Resources.Load<TMP_Settings>("TMP Settings") == null)
        {
            EditorUtility.DisplayDialog("TextMeshPro isn't set up",
                "Import TMP Essentials first: Window ▸ TextMeshPro ▸ Import TMP Essential Resources.", "OK");
            return;
        }
        LayoutFile file;
        try { file = JsonUtility.FromJson<LayoutFile>(json); }
        catch (Exception e)
        {
            EditorUtility.DisplayDialog("Layout error", "The layout file isn't valid JSON:\n\n" + e.Message, "OK");
            return;
        }
        if (file?.rooms == null || file.rooms.Length == 0)
        {
            EditorUtility.DisplayDialog("Layout error", "The layout needs at least one room.", "OK");
            return;
        }
        if (!EditorSceneManager.SaveCurrentModifiedScenesIfUserWantsTo()) return;

        var gen = new SceneGen(file);
        var warnings = gen.Build(robotModel);
        string summary = $"Built '{file.name}': {gen.Count} objects the robot can refer to.";
        foreach (var w in warnings) Debug.LogWarning("[Sports Room] " + w);
        EditorUtility.DisplayDialog("Scene built", warnings.Count == 0 ? summary
            : summary + $"\n\n{warnings.Count} warning(s), also in the Console:\n• " + string.Join("\n• ", warnings.Take(12)), "OK");
    }
}

partial class SceneGen
{
    const string Gen = SportsRoomBuilder.Root + "/Generated";
    const float WallT = 0.2f, DoorH = 2.2f;
    const int SeeThroughLayer = 2;   // "Ignore Raycast": glass and nets don't block the robot's line of sight
    const int CeilingLayer = 1;      // "TransparentFX": hidden from the audience camera so viewers can see into roofed rooms
    const int RobotLayer = 4;        // "Water" (unused otherwise): the robot's body, hidden from its own eye camera

    static readonly Dictionary<string, Vector3> FurnitureSizes = new Dictionary<string, Vector3>
    {
        { "rack", new Vector3(3f, 2f, 0.6f) },          { "cabinet", new Vector3(1.6f, 1.9f, 0.5f) },
        { "locker", new Vector3(0.6f, 1.9f, 0.55f) },   { "bench", new Vector3(2f, 0.45f, 0.45f) },
        { "table", new Vector3(1.6f, 0.8f, 0.8f) },     { "mat", new Vector3(1.8f, 0.02f, 0.8f) },
        { "hoop", new Vector3(1.8f, 3.05f, 1.2f) },     { "goal", new Vector3(3f, 2.2f, 1.5f) },
        { "ball_cart", new Vector3(1f, 0.9f, 0.6f) },   { "treadmill", new Vector3(0.9f, 1.3f, 2f) },
        { "exercise_bike", new Vector3(0.6f, 1.2f, 1.2f) }, { "rowing_machine", new Vector3(0.6f, 0.5f, 2.2f) },
        { "bench_press", new Vector3(1.6f, 1.4f, 1.8f) }, { "squat_rack", new Vector3(1.6f, 2.3f, 1.3f) },
        { "hurdle", new Vector3(1.2f, 0.8f, 0.3f) },    { "box", new Vector3(1f, 1f, 1f) },
    };

    static readonly Dictionary<string, string> DefaultColors = new Dictionary<string, string>
    {
        { "rack", "#8A6F4E" }, { "cabinet", "#5B4636" }, { "locker", "#3B6EA5" }, { "bench", "#A0784C" },
        { "table", "#A0784C" }, { "mat", "#2E86DE" }, { "hoop", "#E8601C" }, { "goal", "#FFFFFF" },
        { "ball_cart", "#4A4A4A" }, { "treadmill", "#3C3C3C" }, { "exercise_bike", "#C0392B" },
        { "rowing_machine", "#2C3E50" }, { "bench_press", "#555555" }, { "squat_rack", "#2C2C2C" },
        { "hurdle", "#F1C40F" }, { "ball", "#E06A1B" }, { "dumbbell", "#444444" }, { "bottle", "#2E86DE" },
        { "racket", "#1F3A93" }, { "bat", "#E8D3A2" }, { "cone", "#FF7F11" }, { "trophy", "#D4AF37" },
        { "medal", "#D4AF37" }, { "key", "#B8B8B8" },
    };

    static readonly Dictionary<int, string[]> LevelNames = new Dictionary<int, string[]>
    {
        { 1, new[] { "" } }, { 2, new[] { "bottom", "top" } }, { 3, new[] { "bottom", "middle", "top" } },
        { 4, new[] { "bottom", "lower", "upper", "top" } },
    };

    readonly LayoutFile L;
    readonly bool urp = GraphicsSettings.currentRenderPipeline != null;
    readonly float wallH;
    readonly Dictionary<string, Interactable> byId = new Dictionary<string, Interactable>(StringComparer.OrdinalIgnoreCase);
    readonly Dictionary<string, Material> mats = new Dictionary<string, Material>();
    readonly Dictionary<string, Mesh> meshes = new Dictionary<string, Mesh>();
    readonly List<string> warnings = new List<string>();
    readonly List<(LayoutDoor def, Vector3 pos, bool alongX)> doorSlots = new List<(LayoutDoor, Vector3, bool)>();
    Transform world, doorsRoot, itemsRoot, roomsRoot;
    readonly List<RoomArea> roomAreas = new List<RoomArea>();

    public int Count => byId.Count;

    public SceneGen(LayoutFile layout)
    {
        L = layout;
        wallH = layout.wall_height > 0 ? layout.wall_height : 3.5f;
    }

    public List<string> Build(GameObject robotModel)
    {
        EnsureFolder(Gen + "/Materials");
        EnsureFolder(Gen + "/Meshes");
        EnsureFolder(SportsRoomBuilder.Root + "/Scenes");
        var scene = EditorSceneManager.NewScene(NewSceneSetup.DefaultGameObjects, NewSceneMode.Single);

        world = new GameObject("World").transform;       // everything here is baked into the NavMesh
        doorsRoot = new GameObject("Doors").transform;   // carve the NavMesh at runtime instead
        itemsRoot = new GameObject("Items").transform;
        roomsRoot = new GameObject("Rooms").transform;   // the floor plan the robot always knows

        southEdge = Bounds().yMin;
        Rooms();
        foreach (var z in L.zones ?? new LayoutZone[0]) Zone(z);
        foreach (var f in L.furniture ?? new LayoutThing[0]) Furniture(f);
        foreach (var d in doorSlots) Door(d.def, d.pos, d.alongX);
        foreach (var i in L.items ?? new LayoutThing[0]) Item(i);
        var robot = Robot(robotModel, out var eye);
        var top = Cameras();
        robot.GetComponent<Perception>().overhead = top;
        Lighting(Bounds());
        WireVisuals(robot, eye, top);
        Ui(robot.GetComponent<RobotAgent>(), eye, top);
        BakeNavMesh();

        EditorSceneManager.SaveScene(scene, SportsRoomBuilder.Root + "/Scenes/SportsRoom.unity");
        AssetDatabase.SaveAssets();
        Selection.activeGameObject = robot;
        return warnings;
    }

    // ---------- rooms, walls, zones ----------

    void Rooms()
    {
        var lines = new Dictionary<(bool alongX, float c), List<Vector2>>();
        void AddEdge(bool alongX, float c, float a, float b)
        {
            var key = (alongX, Mathf.Round(c * 100f) / 100f);
            if (!lines.TryGetValue(key, out var list)) lines[key] = list = new List<Vector2>();
            list.Add(new Vector2(Mathf.Min(a, b), Mathf.Max(a, b)));
        }

        var wallMat = Mat(Col(L.wall_color, "#E9E4DA"), smooth: 0.2f);
        foreach (var r in L.rooms)
        {
            if (r.rect == null || r.rect.Length < 4) { warnings.Add($"room '{r.id}' needs \"rect\": [x, z, width, depth]"); continue; }
            float x = r.rect[0], z = r.rect[1], w = r.rect[2], d = r.rect[3];
            var mid = new Vector3(x + w / 2, 0, z + d / 2);
            Box(world, "floor_" + r.id, mid + Vector3.down * 0.05f, new Vector3(w, 0.1f, d), FloorMat(r, w, d));
            if (!string.IsNullOrEmpty(r.label))
                Label(Decor, r.label, mid + Vector3.up * 0.02f, new Vector3(90, 0, 0), 4.5f, new Color(0.13f, 0.11f, 0.1f, 0.75f), r.label.Length * 0.45f);
            if (string.IsNullOrEmpty(r.id)) warnings.Add("a room has no \"id\"; the robot can't be sent there");
            else
            {
                var area = new GameObject(r.id).AddComponent<RoomArea>();
                area.transform.SetParent(roomsRoot, false);
                area.transform.position = mid;
                area.rect = new Rect(x, z, w, d);
                area.roofed = r.ceiling;
                var rit = area.gameObject.AddComponent<Interactable>();
                rit.id = r.id;
                rit.kind = "room";
                rit.approachPoint = area.transform;
                Register(rit);
                roomAreas.Add(area);
            }
            if (r.ceiling)
            {
                var roof = Box(world, "ceiling_" + r.id, mid + Vector3.up * (wallH + 0.05f), new Vector3(w + WallT, 0.1f, d + WallT), wallMat);
                roof.layer = CeilingLayer;
                roof.AddComponent<NavMeshModifier>().ignoreFromBuild = true;
                Box(world, "lamp_" + r.id, mid + Vector3.up * (wallH - 0.03f), new Vector3(1.2f, 0.06f, 0.3f), Glow("lamp", Color.white, 1.5f), collider: false).layer = CeilingLayer;
                var lamp = new GameObject("light_" + r.id).AddComponent<Light>();
                lamp.transform.SetParent(world, false);
                lamp.transform.position = mid + Vector3.up * (wallH - 0.4f);
                lamp.type = LightType.Point;
                lamp.range = Mathf.Max(w, d) * 1.2f;
                lamp.intensity = 1.3f;
                lamp.color = new Color(1f, 0.97f, 0.92f);
                lamp.shadows = LightShadows.None;
            }
            AddEdge(true, z, x, x + w);
            AddEdge(true, z + d, x, x + w);
            AddEdge(false, x, z, z + d);
            AddEdge(false, x + w, z, z + d);
        }

        var used = new HashSet<LayoutDoor>();
        foreach (var kv in lines)
        {
            bool alongX = kv.Key.alongX;
            float c = kv.Key.c;
            foreach (var seg in Merge(kv.Value))
            {
                var gaps = new List<Vector2>();
                foreach (var d in L.doors ?? new LayoutDoor[0])
                {
                    if (d.at == null || d.at.Length < 2 || used.Contains(d)) continue;
                    float along = alongX ? d.at[0] : d.at[1], across = alongX ? d.at[1] : d.at[0];
                    if (Mathf.Abs(across - c) > 0.25f || along <= seg.x || along >= seg.y) continue;
                    used.Add(d);
                    float w = DoorWidth(d);
                    gaps.Add(new Vector2(along - w / 2, along + w / 2));
                    doorSlots.Add((d, alongX ? new Vector3(along, 0, c) : new Vector3(c, 0, along), alongX));
                }
                gaps.Sort((a, b) => a.x.CompareTo(b.x));
                float start = seg.x - WallT / 2;
                foreach (var g in gaps)
                {
                    WallPiece(alongX, c, start, g.x, 0, wallH, wallMat);
                    WallPiece(alongX, c, g.x, g.y, DoorH, wallH, wallMat);
                    start = g.y;
                }
                WallPiece(alongX, c, start, seg.y + WallT / 2, 0, wallH, wallMat);
            }
        }
        foreach (var d in L.doors ?? new LayoutDoor[0])
            if (!used.Contains(d)) warnings.Add($"door '{d.id}' isn't on any wall; 'at' must sit on a room edge");
    }

    static IEnumerable<Vector2> Merge(List<Vector2> spans)
    {
        var sorted = spans.OrderBy(s => s.x).ToList();
        var cur = sorted[0];
        foreach (var s in sorted.Skip(1))
        {
            if (s.x <= cur.y + 0.01f) cur.y = Mathf.Max(cur.y, s.y);
            else { yield return cur; cur = s; }
        }
        yield return cur;
    }

    void WallPiece(bool alongX, float c, float a, float b, float y0, float y1, Material m)
    {
        if (b - a < 0.01f || y1 - y0 < 0.01f) return;
        float mid = (a + b) / 2, h = y1 - y0;
        var pos = alongX ? new Vector3(mid, y0 + h / 2, c) : new Vector3(c, y0 + h / 2, mid);
        var size = alongX ? new Vector3(b - a, h, WallT) : new Vector3(WallT, h, b - a);
        // The outer wall nearest the audience camera is hidden from it (still solid, still seen by the robot).
        bool hidden = alongX && Mathf.Abs(c - southEdge) < 0.01f;
        var wall = Box(world, "wall", pos, size, m);
        if (hidden) wall.layer = CeilingLayer;
        if (y0 < 0.01f) WallTrim(alongX, c, a, b, hidden);
    }

    // ---------- furniture ----------

    void Furniture(LayoutThing f)
    {
        string kind = (f.kind ?? "box").ToLowerInvariant();
        if (!FurnitureSizes.TryGetValue(kind, out var def))
        {
            warnings.Add($"{f.id}: unknown furniture kind '{f.kind}', built a plain box");
            kind = "box";
            def = FurnitureSizes[kind];
        }
        var root = NewRoot(f, world);
        if (!root) return;
        var s = Size(f.size, def);
        var it = root.gameObject.AddComponent<Interactable>();
        it.id = f.id;
        it.kind = kind.Replace('_', ' ');
        it.approachPoint = Child(root, "approach", new Vector3(0, 0, s.z / 2 + 0.75f));
        var m = Mat(Col(f.color, DefaultColors.TryGetValue(kind, out var dc) ? dc : "#9AA3AD"));
        var dark = Mat(Col("#222222"));
        var steel = Mat(Col("#BBBBBB"), metal: true);

        switch (kind)
        {
            case "rack":
                Shelves(it, root, s, Mathf.Clamp(f.levels > 0 ? f.levels : 3, 1, 4), m);
                if (!string.IsNullOrEmpty(f.label)) RackHeader(root, s, f.label);
                break;

            case "cabinet":
                Shelves(it, root, s, Mathf.Clamp(f.levels > 0 ? f.levels : 2, 1, 4), m);
                it.openable = true;
                it.startsOpen = f.open;
                it.door = Hinge(root, s, Mat(new Color(0.75f, 0.9f, 1f, 0.25f), glass: true), out var glass);
                glass.layer = SeeThroughLayer;
                it.seeThrough = true;
                if (!string.IsNullOrEmpty(f.label)) RackHeader(root, s, f.label);
                break;

            case "locker":
            {
                const float th = 0.03f;
                Box(root, "side_l", new Vector3(-s.x / 2 + th / 2, s.y / 2, 0), new Vector3(th, s.y, s.z), m);
                Box(root, "side_r", new Vector3(s.x / 2 - th / 2, s.y / 2, 0), new Vector3(th, s.y, s.z), m);
                Box(root, "back", new Vector3(0, s.y / 2, -s.z / 2 + th / 2), new Vector3(s.x, s.y, th), m);
                Box(root, "top", new Vector3(0, s.y - th / 2, 0), new Vector3(s.x, th, s.z), m);
                Box(root, "bottom", new Vector3(0, 0.03f, 0), new Vector3(s.x, 0.06f, s.z), m);
                Box(root, "hat_shelf", new Vector3(0, s.y - 0.35f, -0.04f), new Vector3(s.x - 2 * th, 0.02f, s.z - 0.1f), m, collider: false);
                Box(root, "coat_hook", new Vector3(0, s.y - 0.5f, -s.z / 2 + th + 0.03f), new Vector3(0.02f, 0.02f, 0.06f), steel, collider: false);
                it.door = Hinge(root, s, Mat(Shade(m.color, 0.88f)), out _);
                Box(it.door, "handle", new Vector3(s.x - 0.08f, s.y * 0.5f, 0.045f), new Vector3(0.03f, 0.12f, 0.03f), steel);
                for (int v = 0; v < 3; v++)
                    Box(it.door, "vent", new Vector3(s.x / 2, s.y - 0.25f - v * 0.06f, 0.032f), new Vector3(s.x * 0.6f, 0.02f, 0.005f), dark, collider: false);
                it.lockPoint = Child(it.door, "lock", new Vector3(s.x - 0.08f, s.y * 0.42f, 0.04f));
                string number = !string.IsNullOrEmpty(f.label) ? f.label : new string(f.id.Reverse().TakeWhile(ch => char.IsDigit(ch)).Reverse().ToArray());
                if (number.Length > 0) Label(it.door, number, new Vector3(s.x / 2, s.y * 0.78f, 0.036f), new Vector3(0, 180, 0), 1.8f, Color.white, 0.5f);
                MakeSurface(it, root, new Vector3(0, 0.06f, 0), new Vector2(s.x - 0.12f, s.z - 0.12f), 0.22f);
                it.openable = true;
                it.startsOpen = f.open;
                it.locked = f.locked;
                it.keyId = f.key ?? "";
                if (f.locked && string.IsNullOrEmpty(f.key)) warnings.Add($"{f.id} is locked but has no \"key\"");
                break;
            }

            case "bench":
                Box(root, "seat", new Vector3(0, s.y - 0.03f, 0), new Vector3(s.x, 0.06f, s.z), m);
                Box(root, "leg_l", new Vector3(-s.x / 2 + 0.15f, (s.y - 0.06f) / 2, 0), new Vector3(0.06f, s.y - 0.06f, s.z * 0.8f), dark);
                Box(root, "leg_r", new Vector3(s.x / 2 - 0.15f, (s.y - 0.06f) / 2, 0), new Vector3(0.06f, s.y - 0.06f, s.z * 0.8f), dark);
                MakeSurface(it, root, new Vector3(0, s.y, 0), new Vector2(s.x - 0.2f, s.z - 0.1f), 0.45f);
                break;

            case "table":
                Box(root, "top", new Vector3(0, s.y - 0.03f, 0), new Vector3(s.x, 0.06f, s.z), m);
                foreach (var (lx, lz) in new[] { (-1, -1), (-1, 1), (1, -1), (1, 1) })
                    Box(root, "leg", new Vector3(lx * (s.x / 2 - 0.06f), (s.y - 0.06f) / 2, lz * (s.z / 2 - 0.06f)), new Vector3(0.06f, s.y - 0.06f, 0.06f), dark);
                MakeSurface(it, root, new Vector3(0, s.y, 0), new Vector2(s.x - 0.2f, s.z - 0.2f), 0.4f);
                break;

            case "mat":
                Box(root, "mat", new Vector3(0, s.y / 2, 0), s, m);
                MakeSurface(it, root, new Vector3(0, s.y, 0), new Vector2(s.x - 0.2f, s.z - 0.2f), 0.45f);
                break;

            case "hoop":
                Hoop(root, it, s, m);
                break;

            case "goal":
            {
                float hz = s.z / 2;
                var net = Mat(new Color(1, 1, 1, 0.3f), glass: true);
                Cyl(root, "post_l", new Vector3(-s.x / 2, s.y / 2, hz), 0.1f, s.y, m);
                Cyl(root, "post_r", new Vector3(s.x / 2, s.y / 2, hz), 0.1f, s.y, m);
                Cyl(root, "crossbar", new Vector3(0, s.y, hz), 0.1f, s.x + 0.1f, m, new Vector3(0, 0, 90));
                Box(root, "net_back", new Vector3(0, s.y / 2, -hz), new Vector3(s.x, s.y, 0.02f), net).layer = SeeThroughLayer;
                Box(root, "net_top", new Vector3(0, s.y, 0), new Vector3(s.x, 0.02f, s.z), net).layer = SeeThroughLayer;
                Box(root, "net_l", new Vector3(-s.x / 2, s.y / 2, 0), new Vector3(0.02f, s.y, s.z), net).layer = SeeThroughLayer;
                Box(root, "net_r", new Vector3(s.x / 2, s.y / 2, 0), new Vector3(0.02f, s.y, s.z), net).layer = SeeThroughLayer;
                MakeSurface(it, root, new Vector3(0, 0, -0.1f), new Vector2(s.x - 0.6f, s.z - 0.4f), 0.5f);
                break;
            }

            case "ball_cart":
            {
                // Wire basket: the bars have no colliders, so cameras and visibility rays see the balls inside.
                const float bottom = 0.35f, bar = 0.012f, pitch = 0.08f;
                float h = s.y - bottom;
                Box(root, "bin_bottom", new Vector3(0, bottom, 0), new Vector3(s.x, 0.03f, s.z), m);
                foreach (float ry in new[] { bottom + h * 0.5f, s.y })
                {
                    Box(root, "rail", new Vector3(0, ry, s.z / 2), new Vector3(s.x, 0.02f, 0.02f), steel, collider: false);
                    Box(root, "rail", new Vector3(0, ry, -s.z / 2), new Vector3(s.x, 0.02f, 0.02f), steel, collider: false);
                    Box(root, "rail", new Vector3(-s.x / 2, ry, 0), new Vector3(0.02f, 0.02f, s.z), steel, collider: false);
                    Box(root, "rail", new Vector3(s.x / 2, ry, 0), new Vector3(0.02f, 0.02f, s.z), steel, collider: false);
                }
                int nx = Mathf.Max(2, Mathf.RoundToInt(s.x / pitch)), nz = Mathf.Max(2, Mathf.RoundToInt(s.z / pitch));
                for (int i = 0; i <= nx; i++)
                    foreach (float bz in new[] { -s.z / 2, s.z / 2 })
                        Box(root, "bar", new Vector3(-s.x / 2 + i * s.x / nx, bottom + h / 2, bz), new Vector3(bar, h, bar), m, collider: false);
                for (int i = 1; i < nz; i++)
                    foreach (float bx in new[] { -s.x / 2, s.x / 2 })
                        Box(root, "bar", new Vector3(bx, bottom + h / 2, -s.z / 2 + i * s.z / nz), new Vector3(bar, h, bar), m, collider: false);
                foreach (var (lx, lz) in new[] { (-1, -1), (-1, 1), (1, -1), (1, 1) })
                {
                    var p = new Vector3(lx * (s.x / 2 - 0.06f), 0, lz * (s.z / 2 - 0.06f));
                    Box(root, "leg", p + Vector3.up * bottom / 2, new Vector3(0.04f, bottom, 0.04f), steel);
                    Cyl(root, "wheel", p + Vector3.up * 0.05f, 0.1f, 0.04f, dark, new Vector3(0, 0, 90));
                }
                MakeSurface(it, root, new Vector3(0, bottom + 0.03f, 0), new Vector2(s.x - 0.15f, s.z - 0.12f), 0.22f);
                break;
            }

            case "treadmill":
                Box(root, "deck", new Vector3(0, 0.12f, -0.1f), new Vector3(s.x, 0.24f, s.z - 0.2f), m);
                Box(root, "belt", new Vector3(0, 0.245f, -0.1f), new Vector3(s.x - 0.2f, 0.01f, s.z - 0.4f), dark, collider: false);
                Box(root, "upright_l", new Vector3(-s.x / 2 + 0.05f, s.y / 2, s.z / 2 - 0.15f), new Vector3(0.06f, s.y, 0.08f), m);
                Box(root, "upright_r", new Vector3(s.x / 2 - 0.05f, s.y / 2, s.z / 2 - 0.15f), new Vector3(0.06f, s.y, 0.08f), m);
                Box(root, "console", new Vector3(0, s.y - 0.05f, s.z / 2 - 0.2f), new Vector3(s.x, 0.12f, 0.3f), dark);
                Box(root, "screen", new Vector3(0, s.y + 0.012f, s.z / 2 - 0.2f), new Vector3(s.x * 0.5f, 0.006f, 0.16f), Glow("screen", new Color(0.3f, 0.9f, 0.7f), 1.6f), collider: false);
                break;

            case "exercise_bike":
                Box(root, "base", new Vector3(0, 0.04f, 0), new Vector3(0.45f, 0.08f, s.z), dark);
                Cyl(root, "flywheel", new Vector3(0, 0.32f, s.z / 2 - 0.2f), 0.45f, 0.06f, m, new Vector3(0, 0, 90));
                Box(root, "frame", new Vector3(0, 0.45f, 0), new Vector3(0.08f, 0.08f, s.z * 0.7f), m);
                Box(root, "seat_post", new Vector3(0, 0.6f, -s.z / 2 + 0.3f), new Vector3(0.06f, 0.5f, 0.06f), m);
                Box(root, "seat", new Vector3(0, 0.87f, -s.z / 2 + 0.3f), new Vector3(0.25f, 0.06f, 0.3f), dark);
                Box(root, "handle_post", new Vector3(0, 0.75f, s.z / 2 - 0.25f), new Vector3(0.06f, 0.8f, 0.06f), m);
                Box(root, "handlebar", new Vector3(0, s.y - 0.05f, s.z / 2 - 0.25f), new Vector3(0.5f, 0.05f, 0.06f), dark);
                break;

            case "rowing_machine":
                Box(root, "rail", new Vector3(0, 0.25f, -0.15f), new Vector3(0.12f, 0.06f, s.z - 0.4f), m);
                Box(root, "legs_back", new Vector3(0, 0.11f, -s.z / 2 + 0.1f), new Vector3(0.5f, 0.22f, 0.08f), dark);
                Box(root, "seat", new Vector3(0, 0.32f, -0.3f), new Vector3(0.3f, 0.06f, 0.3f), dark);
                Box(root, "footrests", new Vector3(0, 0.2f, s.z / 2 - 0.6f), new Vector3(0.45f, 0.04f, 0.2f), dark);
                Cyl(root, "flywheel", new Vector3(0, 0.35f, s.z / 2 - 0.25f), 0.5f, 0.15f, m, new Vector3(0, 0, 90));
                break;

            case "bench_press":
            {
                float barZ = -s.z / 2 + 0.25f, barY = s.y - 0.2f;
                Box(root, "pad", new Vector3(0, 0.45f, 0), new Vector3(0.3f, 0.08f, s.z * 0.75f), dark);
                Box(root, "pad_leg_f", new Vector3(0, 0.2f, s.z * 0.3f), new Vector3(0.25f, 0.4f, 0.06f), m);
                Box(root, "pad_leg_b", new Vector3(0, 0.2f, -s.z * 0.3f), new Vector3(0.25f, 0.4f, 0.06f), m);
                Box(root, "upright_l", new Vector3(-0.55f, s.y / 2, barZ), new Vector3(0.07f, s.y, 0.07f), m);
                Box(root, "upright_r", new Vector3(0.55f, s.y / 2, barZ), new Vector3(0.07f, s.y, 0.07f), m);
                Cyl(root, "bar", new Vector3(0, barY, barZ), 0.03f, s.x, steel, new Vector3(0, 0, 90));
                Cyl(root, "plate_l", new Vector3(-s.x / 2 + 0.15f, barY, barZ), 0.45f, 0.05f, dark, new Vector3(0, 0, 90));
                Cyl(root, "plate_r", new Vector3(s.x / 2 - 0.15f, barY, barZ), 0.45f, 0.05f, dark, new Vector3(0, 0, 90));
                break;
            }

            case "squat_rack":
            {
                float px = s.x / 2 * 0.85f, pz = s.z / 2 * 0.8f, barY = 1.4f;
                foreach (var (lx, lz) in new[] { (-1, -1), (-1, 1), (1, -1), (1, 1) })
                    Box(root, "post", new Vector3(lx * px, s.y / 2, lz * pz), new Vector3(0.08f, s.y, 0.08f), m);
                Box(root, "beam_f", new Vector3(0, s.y - 0.04f, pz), new Vector3(2 * px, 0.08f, 0.08f), m);
                Box(root, "beam_b", new Vector3(0, s.y - 0.04f, -pz), new Vector3(2 * px, 0.08f, 0.08f), m);
                Cyl(root, "bar", new Vector3(0, barY, pz), 0.03f, s.x + 0.6f, steel, new Vector3(0, 0, 90));
                Cyl(root, "plate_l", new Vector3(-px - 0.2f, barY, pz), 0.45f, 0.06f, dark, new Vector3(0, 0, 90));
                Cyl(root, "plate_r", new Vector3(px + 0.2f, barY, pz), 0.45f, 0.06f, dark, new Vector3(0, 0, 90));
                break;
            }

            case "hurdle":
                Box(root, "post_l", new Vector3(-s.x / 2, s.y / 2, 0), new Vector3(0.04f, s.y, 0.04f), dark);
                Box(root, "post_r", new Vector3(s.x / 2, s.y / 2, 0), new Vector3(0.04f, s.y, 0.04f), dark);
                Box(root, "bar", new Vector3(0, s.y - 0.08f, 0), new Vector3(s.x, 0.16f, 0.02f), m);
                Box(root, "foot_l", new Vector3(-s.x / 2, 0.02f, 0), new Vector3(0.05f, 0.04f, s.z), dark);
                Box(root, "foot_r", new Vector3(s.x / 2, 0.02f, 0), new Vector3(0.05f, 0.04f, s.z), dark);
                break;

            default:
                Box(root, "box", new Vector3(0, s.y / 2, 0), s, m);
                break;
        }

        // Low furniture (benches, carts) must not become walkable platforms; mats stay walkable.
        if (kind != "mat")
        {
            var mod = root.gameObject.AddComponent<NavMeshModifier>();
            mod.overrideArea = true;
            mod.area = 1;   // built-in "Not Walkable"
        }
        Register(it);
    }

    void Shelves(Interactable it, Transform t, Vector3 s, int levels, Material m)
    {
        const float th = 0.04f;
        Box(t, "side_l", new Vector3(-s.x / 2 + th / 2, s.y / 2, 0), new Vector3(th, s.y, s.z), m);
        Box(t, "side_r", new Vector3(s.x / 2 - th / 2, s.y / 2, 0), new Vector3(th, s.y, s.z), m);
        // Viewers look into a rack from behind when it stands against the cut-away wall, so its back is hidden from them.
        var rear = Box(t, "back", new Vector3(0, s.y / 2, -s.z / 2 + th / 2), new Vector3(s.x, s.y, th), m);
        if (FacesAudience(t)) rear.layer = CeilingLayer;
        var lid = Box(t, "top", new Vector3(0, s.y - th / 2, 0), new Vector3(s.x, th, s.z), m);
        if (FacesAudience(t)) lid.layer = CeilingLayer;
        var boardMat = Mat(Shade(m.color, 0.8f));
        var names = LevelNames[levels];
        float gap = (s.y - 0.1f) / levels;
        var area = new Vector2(s.x - 0.2f, s.z - 0.15f);
        for (int k = 0; k < levels; k++)
        {
            float y = 0.08f + k * gap;
            Box(t, "board", new Vector3(0, y - th / 2, 0), new Vector3(s.x - 2 * th, th, s.z - th), boardMat);
            if (levels == 1) { MakeSurface(it, t, new Vector3(0, y, 0), area, 0.4f); break; }
            var lv = Child(t, names[k], new Vector3(0, y, 0));
            var li = lv.gameObject.AddComponent<Interactable>();
            li.id = $"{it.id}_{names[k]}";
            li.kind = "shelf";
            li.partOf = it;
            li.approachPoint = it.approachPoint;
            MakeSurface(li, lv, Vector3.zero, area, 0.4f);
            it.levels.Add(li);
            Register(li);
        }
    }

    // Door that swings outward from the front-left edge; returns the hinge pivot.
    Transform Hinge(Transform t, Vector3 s, Material m, out GameObject panel)
    {
        var hinge = Child(t, "hinge", new Vector3(-s.x / 2, 0, s.z / 2));
        panel = Box(hinge, "door_panel", new Vector3(s.x / 2, s.y / 2, 0.015f), new Vector3(s.x - 0.01f, s.y - 0.02f, 0.03f), m);
        return hinge;
    }

    void MakeSurface(Interactable it, Transform parent, Vector3 local, Vector2 area, float spacing)
    {
        it.canHold = true;
        it.placePoint = Child(parent, "place_point", local);
        it.slotArea = area;
        it.slotSpacing = spacing;
    }

    // ---------- doors between rooms ----------

    void Door(LayoutDoor d, Vector3 pos, bool alongX)
    {
        float w = DoorWidth(d);
        var root = new GameObject(d.id).transform;
        root.SetParent(doorsRoot, false);
        root.SetPositionAndRotation(pos, Quaternion.Euler(0, alongX ? 0 : 90, 0));
        var it = root.gameObject.AddComponent<Interactable>();
        it.id = d.id;
        it.kind = "door";
        it.openable = true;
        it.startsOpen = d.open;
        it.approachPoint = Child(root, "approach_front", new Vector3(0, 0, 1f));
        it.approachPointBack = Child(root, "approach_back", new Vector3(0, 0, -1f));
        it.door = Child(root, "hinge", new Vector3(-w / 2, 0, 0));
        var panel = Box(it.door, "door_panel", new Vector3(w / 2, DoorH / 2, 0), new Vector3(w - 0.04f, DoorH - 0.02f, 0.06f), Mat(Col(d.color, "#7A8A99")));
        var steel = Mat(Col("#CCCCCC"), metal: true);
        Box(it.door, "handle_front", new Vector3(w - 0.15f, 1f, 0.06f), new Vector3(0.12f, 0.03f, 0.04f), steel);
        Box(it.door, "handle_back", new Vector3(w - 0.15f, 1f, -0.06f), new Vector3(0.12f, 0.03f, 0.04f), steel);

        var obstacle = panel.AddComponent<NavMeshObstacle>();
        obstacle.shape = NavMeshObstacleShape.Box;
        obstacle.center = Vector3.zero;
        obstacle.size = Vector3.one;
        obstacle.carving = true;
        obstacle.carveOnlyStationary = false;

        DoorDressing(root, w, Col(d.color, "#7A8A99"));
        if (!string.IsNullOrEmpty(d.sign))
        {
            var ink = Col("#1E2A38");
            Label(root, d.sign, new Vector3(0, DoorH + 0.45f, WallT / 2 + 0.01f), new Vector3(0, 180, 0), 3.5f, ink, 4f);
            Label(root, d.sign, new Vector3(0, DoorH + 0.45f, -WallT / 2 - 0.01f), Vector3.zero, 3.5f, ink, 4f);
        }
        foreach (var side in new[] { pos + root.forward, pos - root.forward })
            foreach (var area in roomAreas)
                if (area.Contains(side) && !area.doors.Contains(it)) area.doors.Add(it);
        Register(it);
    }

    // ---------- pickable items (pivot at the bottom, long axis along local Z) ----------

    void Item(LayoutThing d)
    {
        if (string.IsNullOrEmpty(d.id)) { warnings.Add("an item has no \"id\""); return; }
        string kind = (d.kind ?? "ball").ToLowerInvariant();
        var root = new GameObject(d.id).transform;
        root.SetParent(itemsRoot, false);
        var it = root.gameObject.AddComponent<Interactable>();
        it.id = d.id;
        it.kind = kind.Replace('_', ' ');
        it.pickable = true;
        float k = d.scale > 0 ? d.scale : 1f;
        var main = Col(d.color, DefaultColors.TryGetValue(kind, out var dc) ? dc : "#9AA3AD");

        bool built = false;
        if (!string.IsNullOrEmpty(d.model))
        {
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(d.model);
            if (prefab) built = ModelItem(root, prefab, ItemLength(kind, d) * k, d.id);
            else warnings.Add($"{d.id}: no model found at '{d.model}', using the built-in shape");
        }
        if (!built)
        {
            switch (kind)
            {
                case "ball": Ball(root, d, main, k); break;
                case "dumbbell": Dumbbell(root, main, k); break;
                case "bottle": Bottle(root, d, main, k); break;
                case "racket": Racket(root, d, main, k); break;
                case "bat": Bat(root, d, main, k); break;
                case "cone": Cone(root, main, k); break;
                case "trophy": Trophy(root, main, k); break;
                case "medal": Medal(root, d, main, k); break;
                case "key": Key(root, d, main, k); break;
                default:
                    warnings.Add($"{d.id}: unknown item kind '{d.kind}', built a small box");
                    Box(root, "body", new Vector3(0, 0.1f * k, 0), Vector3.one * 0.2f * k, Mat(main));
                    break;
            }
        }
        root.gameObject.AddComponent<NavMeshModifier>().ignoreFromBuild = true;
        Register(it);

        if (!string.IsNullOrEmpty(d.on))
        {
            if (!byId.TryGetValue(d.on, out var surface)) warnings.Add($"{d.id}: there's nothing called '{d.on}' to put it on");
            else if (!surface.PlaceTarget.canHold) warnings.Add($"{d.id}: '{d.on}' can't hold things");
            else if (!it.PutOn(surface.PlaceTarget)) warnings.Add($"{d.id}: '{d.on}' is full");
            else return;
        }
        if (d.at != null && d.at.Length >= 2) root.SetPositionAndRotation(new Vector3(d.at[0], 0, d.at[1]), Yaw(d.faces));
        else warnings.Add($"{d.id} needs \"at\": [x, z] or \"on\": \"<furniture id>\"");
    }

    // Longest dimension an imported model is scaled to, so any downloaded asset ends up life-sized.
    static float ItemLength(string kind, LayoutThing d)
    {
        switch (kind)
        {
            case "ball": return d.size != null && d.size.Length > 0 ? d.size.Max() : 0.22f;
            case "dumbbell": return 0.35f;
            case "bottle": return 0.25f;
            case "racket": return d.style == "badminton" ? 0.66f : 0.69f;
            case "bat": return 0.86f;
            case "cone": return 0.31f;
            case "trophy": return 0.33f;
            case "medal": return 0.24f;
            case "key": return 0.12f;
            default: return 0.3f;
        }
    }

    bool ModelItem(Transform root, GameObject prefab, float length, string id)
    {
        var model = (GameObject)PrefabUtility.InstantiatePrefab(prefab, root);
        var rends = model.GetComponentsInChildren<Renderer>();
        if (rends.Length == 0)
        {
            warnings.Add($"{id}: model '{prefab.name}' has no visible mesh, using the built-in shape");
            UnityEngine.Object.DestroyImmediate(model);
            return false;
        }
        var b = rends[0].bounds;
        foreach (var r in rends.Skip(1)) b.Encapsulate(r.bounds);
        float s = length / Mathf.Max(b.size.x, b.size.y, b.size.z, 1e-4f);
        model.transform.localScale *= s;
        model.transform.localPosition = new Vector3(-b.center.x, -b.min.y, -b.center.z) * s;
        if (!model.GetComponentInChildren<Collider>())
        {
            var box = root.gameObject.AddComponent<BoxCollider>();
            box.size = b.size * s;
            box.center = Vector3.up * b.size.y * s / 2;
        }
        return true;
    }

    void Ball(Transform root, LayoutThing d, Color main, float k)
    {
        Vector3 sz = d.size != null && d.size.Length >= 3 ? new Vector3(d.size[0], d.size[1], d.size[2])
                   : Vector3.one * (d.size != null && d.size.Length > 0 ? d.size[0] : 0.22f);
        sz *= k;
        string style = (d.style ?? "").ToLowerInvariant();
        var ball = Prim(PrimitiveType.Sphere, root, "ball", new Vector3(0, sz.y / 2, 0), sz,
                        Mat(main, smooth: style == "tennis" ? 0.05f : 0.3f)).transform;
        var accent = Mat(Col(d.color2, style == "tennis" ? "#F5F5F5" : "#151515"), smooth: 0.3f);

        // Seam and panel meshes are built on a unit-diameter sphere, so as children they follow the ball's size and shape.
        switch (style)
        {
            case "basketball":
            {
                var great = Ring(0.503f, 0.012f);
                MeshPart(ball, "seam", great, Vector3.zero, Vector3.one, accent, null, false);
                MeshPart(ball, "seam", great, Vector3.zero, Vector3.one, accent, new Vector3(0, 0, 90), false);
                var side = Ring(0.503f * Mathf.Sin(50 * Mathf.Deg2Rad), 0.012f);
                float off = 0.503f * Mathf.Cos(50 * Mathf.Deg2Rad);
                MeshPart(ball, "seam", side, new Vector3(off, 0, 0), Vector3.one, accent, new Vector3(0, 0, 90), false);
                MeshPart(ball, "seam", side, new Vector3(-off, 0, 0), Vector3.one, accent, new Vector3(0, 0, 90), false);
                break;
            }
            case "tennis":
                MeshPart(ball, "seam", TennisSeam(), Vector3.zero, Vector3.one, accent, null, false);
                break;
            case "soccer":
            {
                var pentagon = Patch(5, 0.182f, 0);
                const float g = 1.618f;
                var dirs = new[] { new Vector3(0, 1, g), new Vector3(0, 1, -g), new Vector3(0, -1, g), new Vector3(0, -1, -g),
                                   new Vector3(1, g, 0), new Vector3(1, -g, 0), new Vector3(-1, g, 0), new Vector3(-1, -g, 0),
                                   new Vector3(g, 0, 1), new Vector3(g, 0, -1), new Vector3(-g, 0, 1), new Vector3(-g, 0, -1) };
                foreach (var dir in dirs)
                    MeshPart(ball, "panel", pentagon, Vector3.zero, Vector3.one, accent, Quaternion.FromToRotation(Vector3.up, dir).eulerAngles, false);
                break;
            }
            case "panels":
            {
                var face = Patch(4, 0.7071f, 45);
                var white = Mat(Col("#F5F5F5"), smooth: 0.35f);
                var faces = new[] { (Vector3.right, accent), (Vector3.left, accent), (Vector3.up, white), (Vector3.down, white),
                                    (Vector3.forward, Mat(main, smooth: 0.35f)), (Vector3.back, Mat(main, smooth: 0.35f)) };
                foreach (var (dir, mat) in faces)
                    MeshPart(ball, "panel", face, Vector3.zero, Vector3.one, mat, Quaternion.FromToRotation(Vector3.up, dir).eulerAngles, false);
                break;
            }
            case "rugby":
            {
                var seam = Ring(0.503f, 0.008f);
                foreach (float a in new[] { 0f, 90f })
                    MeshPart(ball, "seam", seam, Vector3.zero, Vector3.one, accent, new Vector3(0, 0, a), false);
                var lace = Mat(Col("#F5F5F5"), smooth: 0.3f);
                for (int i = 0; i < 6; i++)
                    Box(ball, "lace", new Vector3(0, 0.5f, -0.15f + i * 0.06f), new Vector3(0.14f, 0.025f, 0.022f), lace, collider: false);
                Box(ball, "lace", new Vector3(0, 0.505f, 0), new Vector3(0.02f, 0.02f, 0.34f), lace, collider: false);
                break;
            }
        }
    }

    void Dumbbell(Transform root, Color main, float k)
    {
        float r = 0.065f * k, inner = r * 0.866f, len = 0.075f * k, gap = 0.1f * k;
        var rubber = Mat(main, smooth: 0.2f);
        var chrome = Mat(Col("#D0D0D0"), metal: true, smooth: 0.9f);
        var knurl = Mat(Col("#8A8A8A"), metal: true, smooth: 0.4f);
        var up = new Vector3(90, 0, 0);
        foreach (int side in new[] { -1, 1 })
        {
            MeshPart(root, "hex_head", Prism(6), new Vector3(0, inner, side * (gap + len / 2)), new Vector3(2 * r, len, 2 * r), rubber, up);
            Cyl(root, "collar", new Vector3(0, inner, side * (gap - 0.006f * k)), 0.046f * k, 0.012f * k, chrome, up, collider: false);
        }
        Cyl(root, "handle", new Vector3(0, inner, 0), 0.03f * k, 2 * gap, chrome, up);
        Cyl(root, "grip", new Vector3(0, inner, 0), 0.033f * k, 1.2f * gap, knurl, up, collider: false);
    }

    void Bottle(Transform root, LayoutThing d, Color main, float k)
    {
        var body = Lathe("bottle", P(0, 0), P(0.032f, 0), P(0.036f, 0.004f), P(0.037f, 0.012f), P(0.037f, 0.15f),
                         P(0.035f, 0.172f), P(0.026f, 0.192f), P(0.018f, 0.2f), P(0.018f, 0.21f), P(0, 0.21f));
        MeshPart(root, "body", body, Vector3.zero, Vector3.one * k, Mat(main, smooth: 0.7f));
        MeshPart(root, "label", Lathe("bottle_label", P(0.0378f, 0.05f), P(0.0378f, 0.115f)), Vector3.zero, Vector3.one * k,
                 Mat(Col(d.color2, "#F2F2F2"), smooth: 0.4f), null, false);
        var cap = Lathe("bottle_cap", P(0, 0), P(0.021f, 0), P(0.021f, 0.022f), P(0.012f, 0.03f), P(0.007f, 0.042f), P(0, 0.042f));
        MeshPart(root, "sport_cap", cap, new Vector3(0, 0.205f * k, 0), Vector3.one * k, Mat(Col("#1E1E1E"), smooth: 0.5f), null, false);
    }

    void Racket(Transform root, LayoutThing d, Color main, float k)
    {
        bool badminton = d.style == "badminton";
        float w = (badminton ? 0.2f : 0.26f) * k, l = (badminton ? 0.24f : 0.32f) * k, tube = (badminton ? 0.005f : 0.011f) * k;
        float handleL = (badminton ? 0.2f : 0.27f) * k, shaftL = (badminton ? 0.22f : 0.1f) * k;
        float total = l + shaftL + handleL, y = 0.016f * k;
        float headZ = total / 2 - l / 2, neckZ = headZ - l / 2;
        var frame = Mat(main, smooth: 0.75f);
        var strings = Mat(Col("#F0F0F0"), smooth: 0.3f);
        var up = new Vector3(90, 0, 0);

        MeshPart(root, "frame", Ring(0.5f, tube / w), new Vector3(0, y, headZ), new Vector3(w, w, l), frame);
        int nx = badminton ? 6 : 8, nz = badminton ? 8 : 10;
        for (int i = 1; i <= nx; i++)
        {
            float x = (i / (nx + 1f) - 0.5f) * w, half = l / 2 * Mathf.Sqrt(1 - Mathf.Pow(2 * x / w, 2));
            Box(root, "string", new Vector3(x, y, headZ), new Vector3(0.0018f * k, 0.0018f * k, 2 * half), strings, collider: false);
        }
        for (int i = 1; i <= nz; i++)
        {
            float z = (i / (nz + 1f) - 0.5f) * l, half = w / 2 * Mathf.Sqrt(1 - Mathf.Pow(2 * z / l, 2));
            Box(root, "string", new Vector3(0, y, headZ + z), new Vector3(2 * half, 0.0018f * k, 0.0018f * k), strings, collider: false);
        }

        if (badminton)
            Cyl(root, "shaft", new Vector3(0, y, neckZ - shaftL / 2), 0.008f * k, shaftL, Mat(Col("#C8C8C8"), metal: true, smooth: 0.8f), up, collider: false);
        else
        {
            foreach (int s in new[] { -1, 1 })   // open throat: two arms from the handle up to the frame
            {
                var a = new Vector3(s * w * 0.28f, y, neckZ + 0.02f * k);
                var b = new Vector3(0, y, neckZ - shaftL);
                var arm = Box(root, "throat", (a + b) / 2, new Vector3(0.012f * k, 0.014f * k, Vector3.Distance(a, b)), frame, collider: false);
                arm.transform.localRotation = Quaternion.LookRotation(a - b);
            }
        }
        float handleZ = neckZ - shaftL - handleL / 2;
        MeshPart(root, "grip", Prism(8), new Vector3(0, y, handleZ), new Vector3(0.03f * k, handleL, 0.03f * k), Mat(Col(d.color2, "#1E1E1E"), smooth: 0.2f), up);
        Cyl(root, "butt_cap", new Vector3(0, y, handleZ - handleL / 2), 0.034f * k, 0.008f * k, frame, up, collider: false);
    }

    void Bat(Transform root, LayoutThing d, Color main, float k)
    {
        var up = new Vector3(90, 0, 0);
        if (d.style == "baseball")
        {
            var bat = Lathe("baseball_bat", P(0, 0), P(0.028f, 0), P(0.03f, 0.012f), P(0.015f, 0.03f), P(0.013f, 0.25f),
                            P(0.02f, 0.45f), P(0.032f, 0.64f), P(0.034f, 0.79f), P(0.03f, 0.83f), P(0, 0.84f));
            var pos = new Vector3(0, 0.034f * k, -0.42f * k);
            MeshPart(root, "bat", bat, pos, Vector3.one * k, Mat(main, smooth: 0.65f), up);
            MeshPart(root, "grip_tape", Lathe("bat_tape", P(0.0153f, 0.032f), P(0.0143f, 0.2f)), pos, Vector3.one * k,
                     Mat(Col(d.color2, "#1E1E1E"), smooth: 0.2f), up, false);
        }
        else
        {
            var willow = Mat(main, smooth: 0.35f);
            Box(root, "blade", new Vector3(0, 0.017f * k, 0.13f * k), new Vector3(0.108f, 0.034f, 0.56f) * k, willow);
            Box(root, "spine", new Vector3(0, 0.04f * k, 0.1f * k), new Vector3(0.05f, 0.03f, 0.4f) * k, willow);
            Box(root, "sticker", new Vector3(0, 0.0345f * k, -0.1f * k), new Vector3(0.1f, 0.002f, 0.06f) * k, Mat(Col(d.color2, "#C0392B")), collider: false);
            var handle = Lathe("cricket_handle", P(0, 0), P(0.017f, 0), P(0.017f, 0.29f), P(0.012f, 0.3f), P(0, 0.3f));
            MeshPart(root, "handle", handle, new Vector3(0, 0.017f * k, -0.45f * k), Vector3.one * k, Mat(Col("#1E1E1E"), smooth: 0.2f), up);
        }
    }

    void Cone(Transform root, Color main, float k)
    {
        Box(root, "base", new Vector3(0, 0.0125f * k, 0), new Vector3(0.26f, 0.025f, 0.26f) * k, Mat(Shade(main, 0.85f), smooth: 0.4f));
        var cone = Lathe("cone", P(0, 0.025f), P(0.115f, 0.025f), P(0.11f, 0.035f), P(0.03f, 0.3f), P(0.02f, 0.31f), P(0, 0.31f));
        MeshPart(root, "cone", cone, Vector3.zero, Vector3.one * k, Mat(main, smooth: 0.55f));
        MeshPart(root, "reflective_band", Lathe("cone_band", P(0.0763f, 0.15f), P(0.0612f, 0.2f)), Vector3.zero, Vector3.one * k,
                 Mat(Col("#F4F4F4"), smooth: 0.85f), null, false);
    }

    void Trophy(Transform root, Color main, float k)
    {
        var gold = Mat(main, metal: true, smooth: 0.8f);
        Box(root, "plinth", new Vector3(0, 0.03f * k, 0), new Vector3(0.12f, 0.06f, 0.12f) * k, Mat(Col("#1C1C1C"), smooth: 0.8f));
        Box(root, "plaque", new Vector3(0, 0.03f * k, 0.0605f * k), new Vector3(0.07f, 0.025f, 0.002f) * k, gold, collider: false);
        var cup = Lathe("trophy_cup", P(0, 0), P(0.045f, 0), P(0.045f, 0.01f), P(0.03f, 0.02f), P(0.012f, 0.04f), P(0.01f, 0.1f),
                        P(0.018f, 0.115f), P(0.05f, 0.14f), P(0.068f, 0.2f), P(0.072f, 0.27f), P(0.066f, 0.272f),
                        P(0.06f, 0.21f), P(0.04f, 0.17f), P(0, 0.162f));
        MeshPart(root, "cup", cup, new Vector3(0, 0.06f * k, 0), Vector3.one * k, gold);
        foreach (int s in new[] { -1, 1 })
            MeshPart(root, "handle", Ring(0.5f, 0.09f), new Vector3(s * 0.08f * k, 0.26f * k, 0), new Vector3(0.05f, 0.05f, 0.09f) * k,
                     gold, new Vector3(90, 0, 0), false);
    }

    void Medal(Transform root, LayoutThing d, Color main, float k)
    {
        var gold = Mat(main, metal: true, smooth: 0.85f);
        var disc = Lathe("medal_disc", P(0, 0), P(0.035f, 0), P(0.036f, 0.004f), P(0.033f, 0.006f), P(0.025f, 0.0055f), P(0, 0.007f));
        MeshPart(root, "disc", disc, Vector3.zero, Vector3.one * k, gold);
        MeshPart(root, "loop", Ring(0.5f, 0.15f), new Vector3(0, 0.003f * k, -0.04f * k), Vector3.one * 0.014f * k, gold, null, false);
        var ribbon = Mat(Col(d.color2, "#C0392B"), smooth: 0.3f);
        foreach (int s in new[] { -1, 1 })
        {
            var strap = Box(root, "ribbon", new Vector3(s * 0.025f * k, 0.002f * k, -0.12f * k), new Vector3(0.03f, 0.002f, 0.16f) * k, ribbon, collider: false);
            strap.transform.localRotation = Quaternion.Euler(0, -s * 12f, 0);
        }
    }

    void Key(Transform root, LayoutThing d, Color main, float k)
    {
        var steel = Mat(main, metal: true, smooth: 0.75f);
        float y = 0.005f * k;
        MeshPart(root, "bow", Ring(0.5f, 0.16f), new Vector3(0, y, -0.03f * k), Vector3.one * 0.03f * k, steel);
        Box(root, "shaft", new Vector3(0, y, 0.015f * k), new Vector3(0.008f, 0.004f, 0.055f) * k, steel);
        Box(root, "bit", new Vector3(0.007f * k, y, 0.037f * k), new Vector3(0.008f, 0.004f, 0.006f) * k, steel, collider: false);
        Box(root, "bit", new Vector3(0.006f * k, y, 0.027f * k), new Vector3(0.006f, 0.004f, 0.005f) * k, steel, collider: false);
        Box(root, "bit", new Vector3(0.008f * k, y, 0.017f * k), new Vector3(0.009f, 0.004f, 0.005f) * k, steel, collider: false);
        MeshPart(root, "split_ring", Ring(0.5f, 0.08f), new Vector3(0, y, -0.056f * k), Vector3.one * 0.025f * k, steel, null, false);
        Box(root, "key_tag", new Vector3(0, 0.003f * k, -0.086f * k), new Vector3(0.028f, 0.005f, 0.04f) * k, Mat(Col(d.color2, "#F1C40F"), smooth: 0.5f));
    }

    // ---------- robot, cameras, UI, NavMesh ----------

    GameObject Robot(GameObject model, out Camera eye)
    {
        var r = new GameObject("Robot");
        var at = L.robot?.at;
        var b = Bounds();
        r.transform.SetPositionAndRotation(
            at != null && at.Length >= 2 ? new Vector3(at[0], 0, at[1]) : new Vector3(b.center.x, 0, b.center.y),
            Yaw(L.robot?.faces));
        if (model)
        {
            var m = (GameObject)PrefabUtility.InstantiatePrefab(model, r.transform);
            m.transform.localPosition = Vector3.zero;
            m.transform.localRotation = Quaternion.identity;
        }
        var visuals = r.AddComponent<RobotVisuals>();
        if (!model) BuiltInRobot(r.transform, visuals);

        var hand = Child(r.transform, "Hand", new Vector3(0, 1f, 0.45f));
        var nav = r.AddComponent<NavMeshAgent>();
        nav.radius = 0.35f;
        nav.height = 1.7f;
        nav.speed = 2.5f;
        nav.angularSpeed = 360f;
        nav.acceleration = 8f;
        nav.stoppingDistance = 0.15f;
        var agent = r.AddComponent<RobotAgent>();
        agent.hand = hand;

        eye = new GameObject("RobotEyeCamera").AddComponent<Camera>();
        eye.transform.SetParent(r.transform, false);
        eye.transform.localPosition = new Vector3(0, 1.55f, 0.2f);
        eye.transform.localEulerAngles = new Vector3(12, 0, 0);
        eye.fieldOfView = 70;
        eye.nearClipPlane = 0.05f;
        eye.enabled = false;   // CommandClient turns it on with its own render texture
        agent.eye = eye.transform;
        foreach (var part in r.GetComponentsInChildren<Transform>(true)) part.gameObject.layer = RobotLayer;
        eye.cullingMask &= ~(1 << RobotLayer);

        var perception = r.AddComponent<Perception>();
        perception.eye = eye;
        agent.perception = perception;
        return r;
    }

    Camera Cameras()
    {
        var b = Bounds();
        var center = new Vector3(b.center.x, 0, b.center.y);
        var mainGo = GameObject.FindWithTag("MainCamera");
        if (mainGo && mainGo.TryGetComponent<Camera>(out var main))
        {
            main.rect = new Rect(0, 0, 0.64f, 1);   // the chat panel covers the right side of the screen
            main.fieldOfView = 40;
            main.cullingMask &= ~(1 << CeilingLayer);
            main.clearFlags = CameraClearFlags.SolidColor;
            main.backgroundColor = Col("#12161C");
            PostFx(main);
            FrameAudience(main, b);
        }

        b = Bounds(openOnly: true);
        center = new Vector3(b.center.x, 0, b.center.y);
        var top = new GameObject("OverheadCamera").AddComponent<Camera>();
        const float fov = 60f, aspect = 4f / 3f;
        float tan = Mathf.Tan(fov * 0.5f * Mathf.Deg2Rad);
        float h = Mathf.Max(b.height / (2 * tan), b.width / (2 * tan * aspect)) * 1.08f;
        top.transform.SetPositionAndRotation(center + Vector3.up * h, Quaternion.Euler(90, 0, 0));
        top.fieldOfView = fov;
        top.farClipPlane = h + wallH + 10f;
        top.enabled = false;
        return top;
    }

    void Ui(RobotAgent robot, Camera eye, Camera top)
    {
        var canvas = new GameObject("UI", typeof(RectTransform), typeof(Canvas), typeof(CanvasScaler), typeof(GraphicRaycaster)).GetComponent<Canvas>();
        canvas.renderMode = RenderMode.ScreenSpaceOverlay;
        var scaler = canvas.GetComponent<CanvasScaler>();
        scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
        scaler.referenceResolution = new Vector2(1920, 1080);
        scaler.matchWidthOrHeight = 1f;

        var panel = UiRect("ChatPanel", canvas.transform, new Vector2(0.64f, 0), Vector2.one, Vector2.zero, Vector2.zero);
        panel.gameObject.AddComponent<Image>().color = new Color(0.07f, 0.08f, 0.11f, 0.97f);

        var title = UiRect("Title", panel, new Vector2(0, 1), Vector2.one, new Vector2(16, -64), new Vector2(-16, -12)).gameObject.AddComponent<TextMeshProUGUI>();
        title.text = "<b>Sports Robot</b>  <size=70%><color=#8cf>Qwen3-VL</color></size>";
        title.fontSize = 30;
        title.color = Color.white;
        title.alignment = TextAlignmentOptions.MidlineLeft;

        // What Qwen sees, live: the robot's eye and the overhead camera.
        RawImage Thumb(string caption, float x0, float x1, Vector2 offMin, Vector2 offMax)
        {
            var cap = UiRect(caption + "Caption", panel, new Vector2(x0, 1), new Vector2(x1, 1), new Vector2(offMin.x, -96), new Vector2(offMax.x, -70)).gameObject.AddComponent<TextMeshProUGUI>();
            cap.text = caption;
            cap.fontSize = 18;
            cap.color = new Color(0.6f, 0.8f, 1f);
            var holder = UiRect(caption + "Frame", panel, new Vector2(x0, 1), new Vector2(x1, 1), offMin, offMax);
            holder.gameObject.AddComponent<Image>().color = Color.black;
            var img = UiRect(caption + "View", holder, Vector2.zero, Vector2.one, Vector2.zero, Vector2.zero).gameObject.AddComponent<RawImage>();
            var fit = img.gameObject.AddComponent<AspectRatioFitter>();
            fit.aspectMode = AspectRatioFitter.AspectMode.FitInParent;
            fit.aspectRatio = 4f / 3f;
            return img;
        }
        var eyeView = Thumb("Robot eye", 0, 0.5f, new Vector2(12, -346), new Vector2(-6, -98));
        var topView = Thumb("Overhead", 0.5f, 1, new Vector2(6, -346), new Vector2(-12, -98));

        var status = UiRect("Status", panel, new Vector2(0, 1), Vector2.one, new Vector2(16, -384), new Vector2(-16, -352)).gameObject.AddComponent<TextMeshProUGUI>();
        status.fontSize = 18;
        status.color = new Color(1f, 0.85f, 0.4f);
        status.alignment = TextAlignmentOptions.MidlineLeft;
        status.text = "Press Play";

        var scrollGo = DefaultControls.CreateScrollView(new DefaultControls.Resources());
        scrollGo.name = "Log";
        Place((RectTransform)scrollGo.transform, panel, Vector2.zero, Vector2.one, new Vector2(12, 84), new Vector2(-12, -390));
        scrollGo.GetComponent<Image>().color = new Color(0, 0, 0, 0.35f);
        var scroll = scrollGo.GetComponent<ScrollRect>();
        scroll.horizontal = false;
        if (scroll.horizontalScrollbar)
        {
            UnityEngine.Object.DestroyImmediate(scroll.horizontalScrollbar.gameObject);
            scroll.horizontalScrollbar = null;
        }
        scroll.verticalScrollbarVisibility = ScrollRect.ScrollbarVisibility.AutoHideAndExpandViewport;
        var log = scroll.content.gameObject.AddComponent<TextMeshProUGUI>();
        log.fontSize = 20;
        log.color = new Color(0.9f, 0.92f, 0.95f);
        log.richText = true;
        log.margin = new Vector4(10, 8, 10, 8);
        log.text = "";
        scroll.content.gameObject.AddComponent<ContentSizeFitter>().verticalFit = ContentSizeFitter.FitMode.PreferredSize;

        var inputGo = TMP_DefaultControls.CreateInputField(new TMP_DefaultControls.Resources());
        inputGo.name = "CommandInput";
        Place((RectTransform)inputGo.transform, panel, Vector2.zero, new Vector2(1, 0), new Vector2(12, 12), new Vector2(-12, 72));
        var input = inputGo.GetComponent<TMP_InputField>();
        input.pointSize = 22;
        if (input.placeholder is TMP_Text hint) hint.text = "Tell the robot what to do, then press Enter";

        var es = new GameObject("EventSystem", typeof(EventSystem));
        var newInput = Type.GetType("UnityEngine.InputSystem.UI.InputSystemUIInputModule, Unity.InputSystem");
        if (newInput != null) es.AddComponent(newInput);
        else es.AddComponent<StandaloneInputModule>();

        var client = new GameObject("Controller").AddComponent<CommandClient>();
        client.robot = robot;
        client.perception = robot.GetComponent<Perception>();
        client.robotCamera = eye;
        client.overheadCamera = top;
        client.inputField = input;
        client.logText = log;
        client.statusText = status;
        client.logScroll = scroll;
        client.eyeView = eyeView;
        client.overheadView = topView;
        client.visuals = robot.GetComponent<RobotVisuals>();
    }

    void BakeNavMesh()
    {
        var surface = world.gameObject.AddComponent<NavMeshSurface>();
        surface.collectObjects = CollectObjects.Children;
        surface.BuildNavMesh();
        const string path = Gen + "/NavMesh.asset";
        AssetDatabase.DeleteAsset(path);
        AssetDatabase.CreateAsset(surface.navMeshData, path);
    }

    // ---------- helpers ----------

    Transform NewRoot(LayoutThing f, Transform parent)
    {
        if (string.IsNullOrEmpty(f.id)) { warnings.Add($"a {f.kind} has no \"id\""); return null; }
        if (f.at == null || f.at.Length < 2) { warnings.Add($"{f.id} needs \"at\": [x, z]"); return null; }
        var t = new GameObject(f.id).transform;
        t.SetParent(parent, false);
        t.SetPositionAndRotation(new Vector3(f.at[0], 0, f.at[1]), Yaw(f.faces));
        return t;
    }

    void Register(Interactable it)
    {
        if (byId.ContainsKey(it.id)) warnings.Add($"two objects are called '{it.id}'; ids must be unique");
        byId[it.id] = it;
    }

    Rect Bounds(bool openOnly = false)
    {
        var rooms = L.rooms.Where(r => r.rect != null && r.rect.Length >= 4).ToList();
        if (openOnly && rooms.Any(r => !r.ceiling)) rooms = rooms.Where(r => !r.ceiling).ToList();
        float x0 = float.MaxValue, z0 = float.MaxValue, x1 = float.MinValue, z1 = float.MinValue;
        foreach (var r in rooms)
        {
            x0 = Mathf.Min(x0, r.rect[0]); z0 = Mathf.Min(z0, r.rect[1]);
            x1 = Mathf.Max(x1, r.rect[0] + r.rect[2]); z1 = Mathf.Max(z1, r.rect[1] + r.rect[3]);
        }
        return Rect.MinMaxRect(x0, z0, x1, z1);
    }

    static Quaternion Yaw(string faces)
    {
        switch ((faces ?? "").Trim().ToLowerInvariant())
        {
            case "east": return Quaternion.Euler(0, 90, 0);
            case "south": return Quaternion.Euler(0, 180, 0);
            case "west": return Quaternion.Euler(0, 270, 0);
            default: return Quaternion.Euler(0, float.TryParse(faces, out var deg) ? deg : 0, 0);
        }
    }

    static float DoorWidth(LayoutDoor d) => d.width > 0 ? d.width : 1.6f;
    static Vector3 Size(float[] s, Vector3 def) => s != null && s.Length >= 3 ? new Vector3(s[0], s[1], s[2]) : def;
    static Color Shade(Color c, float f) => new Color(c.r * f, c.g * f, c.b * f, c.a);

    static Color Col(string hex, string fallback = "#9AA3AD")
    {
        if (!string.IsNullOrEmpty(hex) && ColorUtility.TryParseHtmlString(hex, out var c)) return c;
        ColorUtility.TryParseHtmlString(fallback, out c);
        return c;
    }

    static Transform Child(Transform parent, string name, Vector3 local)
    {
        var t = new GameObject(name).transform;
        t.SetParent(parent, false);
        t.localPosition = local;
        return t;
    }

    static GameObject Prim(PrimitiveType type, Transform parent, string name, Vector3 pos, Vector3 scale, Material m,
                           bool collider = true, Vector3? euler = null)
    {
        var g = GameObject.CreatePrimitive(type);
        g.name = name;
        g.transform.SetParent(parent, false);
        g.transform.localPosition = pos;
        g.transform.localScale = scale;
        if (euler.HasValue) g.transform.localEulerAngles = euler.Value;
        g.GetComponent<Renderer>().sharedMaterial = m;
        if (!collider) UnityEngine.Object.DestroyImmediate(g.GetComponent<Collider>());
        return g;
    }

    static GameObject Box(Transform p, string n, Vector3 pos, Vector3 size, Material m, bool collider = true) =>
        Prim(PrimitiveType.Cube, p, n, pos, size, m, collider);

    // Unity's cylinder is 2 units tall along Y, so the length is halved into scale.y.
    static GameObject Cyl(Transform p, string n, Vector3 pos, float dia, float len, Material m, Vector3? euler = null, bool collider = true) =>
        Prim(PrimitiveType.Cylinder, p, n, pos, new Vector3(dia, len / 2, dia), m, collider, euler);

    static void Label(Transform parent, string text, Vector3 pos, Vector3 euler, float fontSize, Color color, float width)
    {
        var g = new GameObject("label_" + text);
        g.transform.SetParent(parent, false);
        g.transform.localPosition = pos;
        g.transform.localEulerAngles = euler;
        var t = g.AddComponent<TextMeshPro>();
        t.text = text;
        t.fontSize = fontSize;
        t.color = color;
        t.alignment = TextAlignmentOptions.Center;
        t.rectTransform.sizeDelta = new Vector2(width, fontSize * 0.15f);
    }

    static RectTransform UiRect(string name, Transform parent, Vector2 anchorMin, Vector2 anchorMax, Vector2 offsetMin, Vector2 offsetMax)
    {
        var rt = new GameObject(name, typeof(RectTransform)).GetComponent<RectTransform>();
        Place(rt, parent, anchorMin, anchorMax, offsetMin, offsetMax);
        return rt;
    }

    static void Place(RectTransform rt, Transform parent, Vector2 anchorMin, Vector2 anchorMax, Vector2 offsetMin, Vector2 offsetMax)
    {
        rt.SetParent(parent, false);
        rt.anchorMin = anchorMin;
        rt.anchorMax = anchorMax;
        rt.offsetMin = offsetMin;
        rt.offsetMax = offsetMax;
    }

    Material Mat(Color c, bool glass = false, bool metal = false, float smooth = 0.5f)
    {
        string key = $"{ColorUtility.ToHtmlStringRGBA(c)}{(glass ? "_glass" : "")}{(metal ? "_metal" : "")}_s{Mathf.RoundToInt(smooth * 100)}";
        if (mats.TryGetValue(key, out var m)) return m;
        string path = $"{Gen}/Materials/{key}.mat";
        m = AssetDatabase.LoadAssetAtPath<Material>(path);
        if (!m)
        {
            m = new Material(Shader.Find(urp ? "Universal Render Pipeline/Lit" : "Standard")) { color = c };
            m.SetFloat(urp ? "_Smoothness" : "_Glossiness", glass ? 0.95f : smooth);
            if (metal) m.SetFloat("_Metallic", 0.85f);
            if (glass) MakeTransparent(m);
            AssetDatabase.CreateAsset(m, path);
        }
        mats[key] = m;
        return m;
    }

    void MakeTransparent(Material m)
    {
        m.SetOverrideTag("RenderType", "Transparent");
        m.SetFloat("_ZWrite", 0);
        m.renderQueue = (int)RenderQueue.Transparent;
        if (urp)
        {
            m.SetFloat("_Surface", 1);
            m.SetFloat("_Blend", 0);
            m.SetFloat("_SrcBlend", (float)BlendMode.SrcAlpha);
            m.SetFloat("_DstBlend", (float)BlendMode.OneMinusSrcAlpha);
            m.EnableKeyword("_SURFACE_TYPE_TRANSPARENT");
        }
        else
        {
            m.SetFloat("_Mode", 3);
            m.SetFloat("_SrcBlend", (float)BlendMode.One);
            m.SetFloat("_DstBlend", (float)BlendMode.OneMinusSrcAlpha);
            m.EnableKeyword("_ALPHAPREMULTIPLY_ON");
        }
    }

    // ---------- procedural meshes (saved under Generated/Meshes so the scene keeps them) ----------

    static Vector2 P(float r, float y) => new Vector2(r, y);

    static GameObject MeshPart(Transform parent, string name, Mesh mesh, Vector3 pos, Vector3 scale, Material m,
                               Vector3? euler = null, bool collider = true)
    {
        var g = new GameObject(name);
        g.transform.SetParent(parent, false);
        g.transform.localPosition = pos;
        g.transform.localScale = scale;
        if (euler.HasValue) g.transform.localEulerAngles = euler.Value;
        g.AddComponent<MeshFilter>().sharedMesh = mesh;
        g.AddComponent<MeshRenderer>().sharedMaterial = m;
        if (collider) g.AddComponent<BoxCollider>();
        return g;
    }

    Mesh Save(string name, List<Vector3> v, List<Vector3> n, List<int> tri)
    {
        var mesh = new Mesh { name = name };
        mesh.SetVertices(v);
        mesh.SetNormals(n);
        mesh.SetTriangles(tri, 0);
        mesh.RecalculateBounds();
        string path = $"{Gen}/Meshes/{name}.asset";
        AssetDatabase.DeleteAsset(path);
        AssetDatabase.CreateAsset(mesh, path);
        meshes[name] = mesh;
        return mesh;
    }

    // Spins a (radius, height) profile around the Y axis: bottles, cones, bats, trophy cups.
    Mesh Lathe(string name, params Vector2[] profile)
    {
        if (meshes.TryGetValue(name, out var cached)) return cached;
        const int seg = 32;
        var v = new List<Vector3>(); var n = new List<Vector3>(); var tri = new List<int>();
        for (int i = 0; i < profile.Length; i++)
        {
            var t = profile[Mathf.Min(profile.Length - 1, i + 1)] - profile[Mathf.Max(0, i - 1)];
            var n2 = new Vector2(t.y, -t.x).normalized;
            for (int j = 0; j <= seg; j++)
            {
                float a = j * 2 * Mathf.PI / seg, c = Mathf.Cos(a), s = Mathf.Sin(a);
                v.Add(new Vector3(profile[i].x * c, profile[i].y, profile[i].x * s));
                n.Add(new Vector3(n2.x * c, n2.y, n2.x * s));
            }
        }
        for (int i = 0; i < profile.Length - 1; i++)
            for (int j = 0; j < seg; j++)
            {
                int a = i * (seg + 1) + j, b = a + 1, c = a + seg + 1, d = c + 1;
                tri.AddRange(new[] { c, b, a, c, d, b });
            }
        return Save(name, v, n, tri);
    }

    // A round tube following a closed loop of points: ball seams, racket frames, key rings.
    Mesh Tube(string name, IList<Vector3> loop, float radius, int sides = 8)
    {
        if (meshes.TryGetValue(name, out var cached)) return cached;
        var v = new List<Vector3>(); var n = new List<Vector3>(); var tri = new List<int>();
        int count = loop.Count;
        for (int i = 0; i <= count; i++)
        {
            var p = loop[i % count];
            var t = (loop[(i + 1) % count] - loop[(i - 1 + count) % count]).normalized;
            var n1 = Vector3.ProjectOnPlane(p, t).normalized;
            var b = Vector3.Cross(t, n1);
            for (int s = 0; s <= sides; s++)
            {
                float a = s * 2 * Mathf.PI / sides;
                var dir = n1 * Mathf.Cos(a) + b * Mathf.Sin(a);
                v.Add(p + dir * radius);
                n.Add(dir);
            }
        }
        for (int i = 0; i < count; i++)
            for (int s = 0; s < sides; s++)
            {
                int a = i * (sides + 1) + s, b = a + 1, c = a + sides + 1, d = c + 1;
                tri.AddRange(new[] { a, b, c, b, d, c });
            }
        return Save(name, v, n, tri);
    }

    Mesh Ring(float major, float tube)
    {
        var pts = new List<Vector3>();
        for (int i = 0; i < 48; i++)
        {
            float a = i * 2 * Mathf.PI / 48;
            pts.Add(new Vector3(Mathf.Cos(a) * major, 0, Mathf.Sin(a) * major));
        }
        return Tube($"ring_{major:F3}_{tube:F4}", pts, tube);
    }

    // The classic curved tennis-ball seam, lying on a unit-diameter sphere.
    Mesh TennisSeam()
    {
        const float a = 0.68f, b = 0.32f;
        var pts = new List<Vector3>();
        for (int i = 0; i < 96; i++)
        {
            float t = i * 2 * Mathf.PI / 96;
            var p = new Vector3(a * Mathf.Cos(t) + b * Mathf.Cos(3 * t), a * Mathf.Sin(t) - b * Mathf.Sin(3 * t), 2 * Mathf.Sqrt(a * b) * Mathf.Sin(2 * t));
            pts.Add(p * 0.503f);
        }
        return Tube("seam_tennis", pts, 0.022f, 6);
    }

    // Flat-sided prism, radius 0.5 and height 1 like Unity's cube: hex dumbbell heads, octagonal racket grips.
    Mesh Prism(int sides)
    {
        string name = "prism_" + sides;
        if (meshes.TryGetValue(name, out var cached)) return cached;
        var v = new List<Vector3>(); var n = new List<Vector3>(); var tri = new List<int>();
        Vector3 Corner(int i, float y) => new Vector3(Mathf.Cos(i * 2 * Mathf.PI / sides) * 0.5f, y, Mathf.Sin(i * 2 * Mathf.PI / sides) * 0.5f);
        for (int i = 0; i < sides; i++)
        {
            Vector3 a0 = Corner(i, -0.5f), a1 = Corner(i + 1, -0.5f), b0 = Corner(i, 0.5f), b1 = Corner(i + 1, 0.5f);
            var fn = new Vector3(a0.x + a1.x, 0, a0.z + a1.z).normalized;
            int s = v.Count;
            v.AddRange(new[] { a0, a1, b0, b1 }); n.AddRange(new[] { fn, fn, fn, fn });
            tri.AddRange(new[] { s + 2, s + 1, s, s + 2, s + 3, s + 1 });
            s = v.Count;
            v.AddRange(new[] { new Vector3(0, 0.5f, 0), b0, b1 }); n.AddRange(new[] { Vector3.up, Vector3.up, Vector3.up });
            tri.AddRange(new[] { s, s + 2, s + 1 });
            s = v.Count;
            v.AddRange(new[] { new Vector3(0, -0.5f, 0), a0, a1 }); n.AddRange(new[] { Vector3.down, Vector3.down, Vector3.down });
            tri.AddRange(new[] { s, s + 1, s + 2 });
        }
        return Save(name, v, n, tri);
    }

    // A polygon bent onto a unit-diameter sphere around +Y: football pentagons, volleyball panels.
    Mesh Patch(int sides, float radius, float spinDeg)
    {
        string name = $"patch_{sides}_{radius:F3}_{spinDeg:F0}";
        if (meshes.TryGetValue(name, out var cached)) return cached;
        // Fine enough, and lifted enough, that its flat triangles never dip below the ball's own facets.
        const int rings = 8, perEdge = 8;
        const float lift = 0.508f;
        int m = sides * perEdge;
        var v = new List<Vector3> { Vector3.up * lift };
        for (int r = 1; r <= rings; r++)
            for (int j = 0; j < m; j++)
            {
                int e = j / perEdge;
                float f = (j % perEdge) / (float)perEdge;
                float a0 = (e * 360f / sides + spinDeg) * Mathf.Deg2Rad, a1 = ((e + 1) * 360f / sides + spinDeg) * Mathf.Deg2Rad;
                var flat = Vector2.Lerp(new Vector2(Mathf.Cos(a0), Mathf.Sin(a0)), new Vector2(Mathf.Cos(a1), Mathf.Sin(a1)), f) * radius * r / rings;
                v.Add(new Vector3(flat.x, 0.5f, flat.y).normalized * lift);
            }
        var n = v.Select(p => p.normalized).ToList();
        var tri = new List<int>();
        for (int j = 0; j < m; j++) tri.AddRange(new[] { 0, 1 + (j + 1) % m, 1 + j });
        for (int r = 1; r < rings; r++)
        {
            int inner = 1 + (r - 1) * m, outer = 1 + r * m;
            for (int j = 0; j < m; j++)
            {
                int a = inner + j, a1 = inner + (j + 1) % m, b = outer + j, b1 = outer + (j + 1) % m;
                tri.AddRange(new[] { a, b1, b, a, a1, b1 });
            }
        }
        return Save(name, v, n, tri);
    }

    static void EnsureFolder(string path)
    {
        var parts = path.Split('/');
        string cur = parts[0];
        for (int i = 1; i < parts.Length; i++)
        {
            string next = cur + "/" + parts[i];
            if (!AssetDatabase.IsValidFolder(next)) AssetDatabase.CreateFolder(cur, parts[i]);
            cur = next;
        }
    }
}
