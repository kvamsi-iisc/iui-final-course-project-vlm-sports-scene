// SportsRoomDecor.cs — the look of the generated scene: floor textures, court markings, wall signs,
// lighting, post-processing for the audience camera, and the built-in robot model.
// It is the second half of SceneGen (see SportsRoomBuilder.cs). Nothing here changes what the robot can do.
using System;
using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEngine;

partial class SceneGen
{
    const int AudienceOnlyLayer = 5;   // "UI": path line, view cone and highlights; hidden from both robot cameras

    readonly Dictionary<string, Texture2D> textures = new Dictionary<string, Texture2D>();
    Transform decorRoot, zonesRoot;
    float southEdge;                   // the wall nearest the audience camera is cut away for viewers

    // Decoration lives outside "World", so it never changes the baked NavMesh.
    Transform Decor => decorRoot ? decorRoot : decorRoot = new GameObject("Decor").transform;

    static void SetLayer(Transform t, int layer)
    {
        foreach (var c in t.GetComponentsInChildren<Transform>(true)) c.gameObject.layer = layer;
    }

    static string Slug(string s) =>
        new string((s ?? "x").ToLowerInvariant().Select(ch => char.IsLetterOrDigit(ch) ? ch : '_').ToArray());

    // ---------- procedural textures and special materials ----------

    static float Hash(int a, int b)
    {
        unchecked
        {
            uint h = (uint)(a * 374761393 + b * 668265263);
            h = (h ^ (h >> 13)) * 1274126177u;
            return ((h ^ (h >> 16)) & 0xFFFF) / 65535f;
        }
    }

    // Greyscale tile (white = the material's own colour), 2 m x 2 m of floor per repeat.
    Texture2D Tex(string name, Func<int, int, float> shade)
    {
        if (textures.TryGetValue(name, out var cached)) return cached;
        const int size = 256;
        var tex = new Texture2D(size, size, TextureFormat.RGB24, true)
            { name = name, wrapMode = TextureWrapMode.Repeat, filterMode = FilterMode.Trilinear, anisoLevel = 8 };
        var px = new Color32[size * size];
        for (int y = 0; y < size; y++)
            for (int x = 0; x < size; x++)
            {
                byte b = (byte)Mathf.Clamp(Mathf.RoundToInt(shade(x, y) * 255f), 0, 255);
                px[y * size + x] = new Color32(b, b, b, 255);
            }
        tex.SetPixels32(px);
        tex.Apply(true);
        EnsureFolder(Gen + "/Textures");
        string path = $"{Gen}/Textures/{name}.asset";
        AssetDatabase.DeleteAsset(path);
        AssetDatabase.CreateAsset(tex, path);
        textures[name] = tex;
        return tex;
    }

    Texture2D WoodTex => Tex("wood_planks", (x, y) =>
    {
        int row = y / 32, start = (int)(Hash(row, 7) * 256), u = (x - start + 256) % 256;
        float plank = 0.9f + 0.08f * Hash(row * 5 + u / 128, 11), grain = (Hash(y, 3) - 0.5f) * 0.03f;
        return (plank + grain) * (u % 128 == 0 || y % 32 == 0 ? 0.8f : 1f);
    });

    Texture2D RubberTex => Tex("rubber_speckle", (x, y) => Hash(x, y) > 0.965f ? 1f : 0.8f + 0.06f * Hash(y, x));

    Texture2D TileTex => Tex("floor_tiles", (x, y) =>
        x % 64 < 2 || y % 64 < 2 ? 0.7f : 0.9f + 0.08f * Hash(x / 64, y / 64 + 40));

    Shader LitShader => Shader.Find(urp ? "Universal Render Pipeline/Lit" : "Standard");

    Material NewMat(string name, Shader shader, Color c)
    {
        string path = $"{Gen}/Materials/{name}.mat";
        AssetDatabase.DeleteAsset(path);
        var m = new Material(shader) { color = c };
        AssetDatabase.CreateAsset(m, path);
        return m;
    }

    Material TexMat(string name, Color tint, Texture2D tex, float width, float depth, float smooth)
    {
        var m = NewMat("tex_" + name, LitShader, tint);
        m.mainTexture = tex;
        m.mainTextureScale = new Vector2(width / 2f, depth / 2f);
        m.SetFloat(urp ? "_Smoothness" : "_Glossiness", smooth);
        return m;
    }

    // Self-lit: screens, status lights, lamps. Bloom on the audience camera makes these glow.
    Material Glow(string name, Color c, float intensity = 2f)
    {
        if (mats.TryGetValue("glow_" + name, out var cached)) return cached;
        var m = NewMat("glow_" + name, LitShader, c);
        m.EnableKeyword("_EMISSION");
        m.SetColor("_EmissionColor", c * intensity);
        m.globalIlluminationFlags = MaterialGlobalIlluminationFlags.RealtimeEmissive;
        return mats["glow_" + name] = m;
    }

    // Flat see-through colour for the audience-only overlays.
    Material Overlay(string name, Color c)
    {
        var m = NewMat("overlay_" + name, Shader.Find(urp ? "Universal Render Pipeline/Unlit" : "Sprites/Default"), c);
        if (urp) { MakeTransparent(m); m.SetFloat("_Cull", 0f); }   // visible from both sides
        return m;
    }

    Material FloorMat(LayoutRoom r, float w, float d)
    {
        var c = Col(r.floor_color, "#C8A878");
        switch ((r.floor ?? "").ToLowerInvariant())
        {
            case "wood": return TexMat("floor_" + Slug(r.id), c, WoodTex, w, d, 0.6f);
            case "tile": return TexMat("floor_" + Slug(r.id), c, TileTex, w, d, 0.7f);
            case "rubber": return TexMat("floor_" + Slug(r.id), c, RubberTex, w, d, 0.25f);
            default: return Mat(c, smooth: 0.6f);
        }
    }

    // ---------- zones: painted floor areas with markings, a floor label and a wall sign ----------

    void Zone(LayoutZone z)
    {
        if (z.rect == null || z.rect.Length < 4) { warnings.Add($"zone '{z.label}' needs \"rect\": [x, z, width, depth]"); return; }
        float x = z.rect[0], zz = z.rect[1], w = z.rect[2], d = z.rect[3];
        string style = (z.style ?? "").ToLowerInvariant(), slug = Slug(z.label);
        var c = Col(z.color, "#888888");
        var mid = new Vector3(x + w / 2, 0, zz + d / 2);
        var fill = style == "basketball" ? TexMat("zone_" + slug, c, WoodTex, w, d, 0.75f)
                 : style == "gym" ? TexMat("zone_" + slug, c, RubberTex, w, d, 0.25f) : Mat(c);
        Box(world, "zone_" + z.label, mid + Vector3.up * 0.003f, new Vector3(w, 0.006f, d), fill, collider: false);

        if (style == "basketball")
        {
            // Court frame: origin on the baseline under the hoop, +z pointing into the court.
            string side = (z.hoop ?? "north").ToLowerInvariant();
            bool ns = side == "north" || side == "south";
            var origin = side == "north" ? new Vector3(mid.x, 0, zz + d) : side == "south" ? new Vector3(mid.x, 0, zz)
                       : side == "east" ? new Vector3(x + w, 0, mid.z) : new Vector3(x, 0, mid.z);
            var court = Child(Decor, "court_lines", origin);
            court.rotation = Yaw(side) * Quaternion.Euler(0, 180, 0);
            CourtLines(court, ns ? w : d, ns ? d : w, Shade(c, 0.72f));
        }
        else
        {
            var edge = Child(Decor, "zone_border_" + slug, mid);
            var paint = Mat(Col(style == "gym" ? "#E8B923" : "#F4F1EA"), smooth: 0.4f);
            const float lw = 0.07f, inset = 0.12f, y = 0.012f;
            float hw = w / 2 - inset, hd = d / 2 - inset;
            Box(edge, "line", new Vector3(0, y, hd), new Vector3(2 * hw + lw, 0.006f, lw), paint, collider: false);
            Box(edge, "line", new Vector3(0, y, -hd), new Vector3(2 * hw + lw, 0.006f, lw), paint, collider: false);
            Box(edge, "line", new Vector3(hw, y, 0), new Vector3(lw, 0.006f, 2 * hd + lw), paint, collider: false);
            Box(edge, "line", new Vector3(-hw, y, 0), new Vector3(lw, 0.006f, 2 * hd + lw), paint, collider: false);
        }

        if (!string.IsNullOrEmpty(z.label))
        {
            // The name is painted on the hall floor just outside the zone, so it never fights the markings.
            const float h = 0.42f;
            bool below = zz - 1f > southEdge;
            var at = new Vector3(mid.x, 0.02f, below ? zz - 0.45f : zz + d + 0.45f);
            Label(Decor, z.label, at, new Vector3(90, 0, 0), h * 10, new Color(0.13f, 0.11f, 0.1f, 0.8f), z.label.Length * h);
            if (z.sign_at != null && z.sign_at.Length >= 2) WallSign(z.label, z.sign_at[0], z.sign_at[1], z.sign_faces, c);

            if (!zonesRoot) zonesRoot = new GameObject("Zones").transform;
            var zone = new GameObject(z.label.ToLowerInvariant()).AddComponent<FloorZone>();
            zone.transform.SetParent(zonesRoot, false);
            zone.transform.position = mid;
            zone.label = z.label.ToLowerInvariant();
            zone.rect = new Rect(x, zz, w, d);
        }
    }

    // Half-court markings in the court frame: x across, z away from the baseline.
    void CourtLines(Transform t, float W, float D, Color keyPaint)
    {
        var white = Mat(Col("#F4F1EA"), smooth: 0.4f);
        const float lw = 0.06f, y = 0.012f, hoopZ = 0.55f;
        void Line(float x0, float z0, float x1, float z1) =>
            Box(t, "line", new Vector3((x0 + x1) / 2, y, (z0 + z1) / 2),
                new Vector3(Mathf.Abs(x1 - x0) + lw, 0.006f, Mathf.Abs(z1 - z0) + lw), white, collider: false);
        void Arc(float cz, float r, float a0, float a1) =>
            MeshPart(t, "arc", ArcMesh(r, lw, a0, a1), new Vector3(0, y, cz), Vector3.one, white, null, false);

        float hw = W / 2, keyW = Mathf.Min(3.6f, W * 0.3f), keyD = Mathf.Min(5.8f, D * 0.62f);
        Line(-hw, 0, hw, 0); Line(-hw, D, hw, D); Line(-hw, 0, -hw, D); Line(hw, 0, hw, D);

        Box(t, "key_paint", new Vector3(0, 0.0085f, keyD / 2), new Vector3(keyW, 0.004f, keyD), Mat(keyPaint, smooth: 0.7f), collider: false);
        Line(-keyW / 2, 0, -keyW / 2, keyD); Line(keyW / 2, 0, keyW / 2, keyD); Line(-keyW / 2, keyD, keyW / 2, keyD);
        Arc(keyD, keyW / 2, 0, 360);                  // free-throw circle
        Arc(hoopZ, 1.25f, 0, 180);                    // no-charge arc under the hoop

        float three = Mathf.Min(6.75f, hw - 0.9f);    // three-point line, pulled in to fit a narrow court
        if (hoopZ + three < D - 0.3f)
        {
            Arc(hoopZ, three, 0, 180);
            Line(-three, 0, -three, hoopZ); Line(three, 0, three, hoopZ);
        }
        Arc(D, Mathf.Min(1.8f, W * 0.14f), 180, 360); // half of the centre circle on the far line
    }

    // Flat curved stripe lying on the floor, angles in degrees from +x towards +z.
    Mesh ArcMesh(float r, float width, float a0, float a1)
    {
        string name = $"arc_{Mathf.RoundToInt(r * 100)}_{Mathf.RoundToInt(width * 100)}_{Mathf.RoundToInt(a0)}_{Mathf.RoundToInt(a1)}";
        if (meshes.TryGetValue(name, out var cached)) return cached;
        int seg = Mathf.Max(8, Mathf.CeilToInt(Mathf.Abs(a1 - a0) / 4f));
        var v = new List<Vector3>(); var n = new List<Vector3>(); var tri = new List<int>();
        for (int i = 0; i <= seg; i++)
        {
            float a = Mathf.Lerp(a0, a1, i / (float)seg) * Mathf.Deg2Rad;
            var dir = new Vector3(Mathf.Cos(a), 0, Mathf.Sin(a));
            v.Add(dir * (r - width / 2)); v.Add(dir * (r + width / 2));
            n.Add(Vector3.up); n.Add(Vector3.up);
        }
        for (int i = 0; i < seg; i++)
        {
            int a = i * 2;   // both windings, so the stripe shows whichever way the arc runs
            tri.AddRange(new[] { a, a + 1, a + 2, a + 2, a + 1, a + 3, a, a + 2, a + 1, a + 2, a + 3, a + 1 });
        }
        return Save(name, v, n, tri);
    }

    // ---------- walls, signs, doors ----------

    bool FacesAudience(Transform t) => t.forward.z > 0.7f;   // its front points north, so viewers look at its back

    void WallSign(string text, float x, float z, string faces, Color color)
    {
        var root = Child(Decor, "sign_" + text, new Vector3(x, wallH - 0.62f, z));
        root.rotation = Yaw(faces);
        float width = text.Length * 0.27f + 0.7f;
        Box(root, "board", new Vector3(0, 0, 0.03f), new Vector3(width, 0.64f, 0.05f), Mat(Shade(color, 0.5f), smooth: 0.3f), collider: false);
        Box(root, "stripe", new Vector3(0, -0.27f, 0.057f), new Vector3(width - 0.2f, 0.035f, 0.006f), Mat(color), collider: false);
        Label(root, text, new Vector3(0, 0.03f, 0.06f), new Vector3(0, 180, 0), 4.2f, Color.white, width);
        if (FacesAudience(root)) SetLayer(root, CeilingLayer);
    }

    // Skirting board and a colour band give every wall a visible bottom edge and a sense of height.
    void WallTrim(bool alongX, float c, float a, float b, bool hidden)
    {
        float mid = (a + b) / 2, len = b - a;
        void Band(string name, float y, float h, float extra, Material m)
        {
            var pos = alongX ? new Vector3(mid, y, c) : new Vector3(c, y, mid);
            var size = alongX ? new Vector3(len, h, WallT + extra) : new Vector3(WallT + extra, h, len);
            var g = Box(Decor, name, pos, size, m, collider: false);
            if (hidden) g.layer = CeilingLayer;
        }
        Band("skirting", 0.07f, 0.14f, 0.03f, Mat(Col("#39404A"), smooth: 0.4f));
        Band("wall_band", 1.15f, 0.12f, 0.012f, Mat(Col(L.wall_accent, "#2F6DB5"), smooth: 0.4f));
    }

    void DoorDressing(Transform root, float w, Color color)
    {
        var frame = Mat(Col("#2B313A"), smooth: 0.5f);
        float t = WallT + 0.06f;
        Box(root, "frame_l", new Vector3(-w / 2 - 0.04f, DoorH / 2, 0), new Vector3(0.08f, DoorH, t), frame, collider: false);
        Box(root, "frame_r", new Vector3(w / 2 + 0.04f, DoorH / 2, 0), new Vector3(0.08f, DoorH, t), frame, collider: false);
        Box(root, "frame_top", new Vector3(0, DoorH + 0.04f, 0), new Vector3(w + 0.16f, 0.08f, t), frame, collider: false);
        var mat = Mat(Shade(color, 0.8f), smooth: 0.2f);
        foreach (int side in new[] { -1, 1 })   // a coloured mat on each side marks the doorway on the floor
            Box(root, "door_mat", new Vector3(0, 0.008f, side * 0.6f), new Vector3(w + 0.3f, 0.012f, 0.8f), mat, collider: false);
        float board = w + 1.4f;
        foreach (int side in new[] { -1, 1 })
            Box(root, "sign_board", new Vector3(0, DoorH + 0.45f, side * (WallT / 2 + 0.004f)), new Vector3(board, 0.5f, 0.006f),
                Mat(Shade(color, 1.25f), smooth: 0.3f), collider: false);
    }

    // ---------- lighting, backdrop, post-processing ----------

    void Lighting(Rect b)
    {
        var sun = UnityEngine.Object.FindObjectsByType<Light>(FindObjectsSortMode.None).FirstOrDefault(l => l.type == LightType.Directional);
        if (sun)
        {
            sun.transform.rotation = Quaternion.Euler(52, 28, 0);   // from the south-west, so shadows fall away from viewers
            sun.color = new Color(1f, 0.96f, 0.9f);
            sun.intensity = 1.15f;
            sun.shadows = LightShadows.Soft;
            sun.shadowStrength = 0.6f;
        }
        RenderSettings.ambientMode = UnityEngine.Rendering.AmbientMode.Trilight;
        RenderSettings.ambientSkyColor = new Color(0.62f, 0.66f, 0.72f);
        RenderSettings.ambientEquatorColor = new Color(0.5f, 0.5f, 0.52f);
        RenderSettings.ambientGroundColor = new Color(0.3f, 0.28f, 0.26f);

        // A dark plinth under the building turns the cut-away into a clean model on a table.
        Box(Decor, "plinth", new Vector3(b.center.x, -0.3f, b.center.y), new Vector3(b.width + 1.6f, 0.39f, b.height + 1.6f),
            Mat(Col("#20252D"), smooth: 0.3f), collider: false);
    }

    void FrameAudience(Camera cam, Rect b)
    {
        var fit = cam.gameObject.AddComponent<AudienceCamera>();
        fit.building = b;
        fit.wallHeight = wallH;
        fit.Fit(0.64f * 16f / 9f);   // the share of a 16:9 screen the chat panel leaves free; refits itself in Play mode
    }

    // Bloom, tone mapping and a soft vignette for the audience camera only: the robot's cameras stay raw.
    // Done by name so the file still compiles in a project without the Universal Render Pipeline.
    void PostFx(Camera cam)
    {
        if (!urp) return;
        try
        {
            const string core = "Unity.RenderPipelines.Core.Runtime", universal = "Unity.RenderPipelines.Universal.Runtime";
            var volumeType = Type.GetType("UnityEngine.Rendering.Volume, " + core);
            var profileType = Type.GetType("UnityEngine.Rendering.VolumeProfile, " + core);
            var camData = Type.GetType("UnityEngine.Rendering.Universal.UniversalAdditionalCameraData, " + universal);
            if (volumeType == null || profileType == null || camData == null) return;

            var profile = ScriptableObject.CreateInstance(profileType);
            string path = Gen + "/AudienceLook.asset";
            AssetDatabase.DeleteAsset(path);
            AssetDatabase.CreateAsset(profile, path);
            var add = profileType.GetMethod("Add", new[] { typeof(Type), typeof(bool) });
            void Effect(string type, params (string field, object value)[] settings)
            {
                var t = Type.GetType($"UnityEngine.Rendering.Universal.{type}, {universal}");
                if (t == null) return;
                var fx = (ScriptableObject)add.Invoke(profile, new object[] { t, false });
                foreach (var (field, value) in settings)
                {
                    var p = t.GetField(field)?.GetValue(fx);
                    if (p == null) continue;
                    var valueProp = p.GetType().GetProperty("value");
                    valueProp.SetValue(p, valueProp.PropertyType.IsEnum ? Enum.ToObject(valueProp.PropertyType, value) : value);
                    p.GetType().GetProperty("overrideState").SetValue(p, true);
                }
                AssetDatabase.AddObjectToAsset(fx, profile);
            }
            Effect("Tonemapping", ("mode", 1));                                   // Neutral
            Effect("Bloom", ("threshold", 1.0f), ("intensity", 0.55f), ("scatter", 0.6f));
            Effect("Vignette", ("intensity", 0.22f), ("smoothness", 0.45f));
            Effect("ColorAdjustments", ("contrast", 8f), ("saturation", 10f));
            EditorUtility.SetDirty(profile);

            var holder = new GameObject("AudienceLook") { layer = AudienceOnlyLayer };
            var volume = holder.AddComponent(volumeType);
            volumeType.GetProperty("isGlobal").SetValue(volume, true);
            volumeType.GetField("sharedProfile").SetValue(volume, profile);

            var data = cam.GetComponent(camData) ?? cam.gameObject.AddComponent(camData);
            camData.GetProperty("renderPostProcessing").SetValue(data, true);
            var aa = camData.GetProperty("antialiasing");
            aa.SetValue(data, Enum.ToObject(aa.PropertyType, 2));                 // SMAA
            camData.GetProperty("volumeLayerMask")?.SetValue(data, (LayerMask)(1 << AudienceOnlyLayer));
        }
        catch (Exception e) { warnings.Add("couldn't set up post-processing for the audience camera: " + e.Message); }
    }

    // ---------- furniture details ----------

    void Hoop(Transform root, Interactable it, Vector3 s, Material m)
    {
        float back = -s.z / 2, boardZ = back + 0.55f, boardY = s.y + 0.4f;
        var dark = Mat(Col("#22262C"), smooth: 0.4f);
        var white = Mat(Col("#FFFFFF"), smooth: 0.6f);
        var pad = Mat(Col("#2456A6"), smooth: 0.2f);
        Box(root, "base", new Vector3(0, 0.09f, back + 0.2f), new Vector3(0.8f, 0.18f, 0.5f), dark, collider: false);
        Cyl(root, "pole", new Vector3(0, (s.y + 0.5f) / 2, back + 0.08f), 0.13f, s.y + 0.5f, dark);
        Cyl(root, "pole_padding", new Vector3(0, 0.95f, back + 0.08f), 0.26f, 1.7f, pad, collider: false);
        Box(root, "arm", new Vector3(0, s.y + 0.35f, back + 0.3f), new Vector3(0.08f, 0.08f, 0.45f), dark);
        Box(root, "backboard", new Vector3(0, boardY, boardZ), new Vector3(s.x, 1.05f, 0.04f), white);

        // Painted on the front of the board: the outer border and the shooter's square.
        void Outline(float w, float h, float cy, float line, Material paint)
        {
            float z = boardZ + 0.022f;
            Box(root, "board_line", new Vector3(0, cy + h / 2, z), new Vector3(w, line, 0.006f), paint, collider: false);
            Box(root, "board_line", new Vector3(0, cy - h / 2, z), new Vector3(w, line, 0.006f), paint, collider: false);
            Box(root, "board_line", new Vector3(-w / 2, cy, z), new Vector3(line, h + line, 0.006f), paint, collider: false);
            Box(root, "board_line", new Vector3(w / 2, cy, z), new Vector3(line, h + line, 0.006f), paint, collider: false);
        }
        Outline(s.x - 0.06f, 0.99f, boardY, 0.05f, dark);
        Outline(0.59f, 0.45f, s.y + 0.2f, 0.05f, m);

        var rim = new Vector3(0, s.y, back + 0.82f);
        Box(root, "rim_bracket", new Vector3(0, s.y - 0.01f, (boardZ + rim.z - 0.23f) / 2 + 0.01f), new Vector3(0.12f, 0.04f, rim.z - 0.23f - boardZ), m, collider: false);
        MeshPart(root, "rim", Ring(0.5f, 0.02f), rim, Vector3.one * 0.46f, m, null, false);
        var net = Lathe("hoop_net", P(0.225f, 0), P(0.2f, -0.12f), P(0.16f, -0.28f), P(0.15f, -0.4f));
        MeshPart(root, "net", net, rim, Vector3.one, Mat(new Color(1, 1, 1, 0.45f), glass: true), null, false);
        MakeSurface(it, root, rim + Vector3.down * 0.3f, new Vector2(0.3f, 0.3f), 0.3f);
    }

    // Name plate on top of a rack, so both the viewers and the robot's eye can read what it stores.
    void RackHeader(Transform root, Vector3 s, string text)
    {
        float width = Mathf.Min(s.x, text.Length * 0.1f + 0.3f), z = s.z / 2 - 0.03f, y = s.y + 0.11f;
        Box(root, "header", new Vector3(0, y, z), new Vector3(width, 0.2f, 0.03f), Mat(Col("#1E2A38"), smooth: 0.3f), collider: false);
        Label(root, text, new Vector3(0, y, z + 0.018f), new Vector3(0, 180, 0), 1.4f, Color.white, s.x);
    }

    // ---------- the built-in robot: a wheeled service robot that carries things on a tray ----------

    static GameObject Link(Transform p, string n, Vector3 a, Vector3 b, float dia, Material m)
    {
        var g = Prim(PrimitiveType.Cylinder, p, n, (a + b) / 2, new Vector3(dia, Vector3.Distance(a, b) / 2, dia), m, false);
        g.transform.localRotation = Quaternion.FromToRotation(Vector3.up, b - a);
        return g;
    }

    void BuiltInRobot(Transform r, RobotVisuals vis)
    {
        var shell = Mat(Col("#F1F3F6"), smooth: 0.7f);
        var accent = Mat(Col("#1F6FD1"), smooth: 0.6f);
        var dark = Mat(Col("#1B2027"), smooth: 0.6f);
        var glass = Mat(Col("#0B0F14"), smooth: 0.95f);
        var joint = Mat(Col("#8E98A5"), metal: true, smooth: 0.7f);
        var front = Mat(Col("#FF7A1A"), smooth: 0.5f);   // orange = the robot's front, easy to spot from above
        var status = Glow("robot_status", new Color(0.2f, 0.85f, 1f), 2.5f);
        var lights = new List<Renderer>();
        void Lit(GameObject g) => lights.Add(g.GetComponent<Renderer>());

        // Drive base
        var skirt = Lathe("robot_base", P(0, 0.03f), P(0.3f, 0.03f), P(0.34f, 0.06f), P(0.35f, 0.16f), P(0.33f, 0.24f), P(0.24f, 0.3f), P(0, 0.3f));
        MeshPart(r, "base", skirt, Vector3.zero, Vector3.one, dark, null, false);
        Lit(MeshPart(r, "status_ring", Ring(0.5f, 0.02f), new Vector3(0, 0.2f, 0), Vector3.one * 0.71f, status, null, false));
        foreach (int side in new[] { -1, 1 })
            Cyl(r, "wheel", new Vector3(side * 0.33f, 0.1f, 0), 0.2f, 0.06f, glass, new Vector3(0, 0, 90), collider: false);
        Box(r, "front_bumper", new Vector3(0, 0.11f, 0.3f), new Vector3(0.2f, 0.05f, 0.1f), front, collider: false);

        // Body
        var torso = Lathe("robot_torso", P(0, 0.28f), P(0.2f, 0.28f), P(0.23f, 0.5f), P(0.25f, 0.85f), P(0.24f, 1.05f), P(0.2f, 1.18f), P(0.1f, 1.24f), P(0, 1.24f));
        MeshPart(r, "torso", torso, Vector3.zero, Vector3.one, shell, null, false);
        MeshPart(r, "belt", Lathe("robot_belt", P(0.24f, 0.58f), P(0.247f, 0.66f)), Vector3.zero, Vector3.one, accent, null, false);
        Box(r, "chest_screen", new Vector3(0, 1f, 0.225f), new Vector3(0.22f, 0.13f, 0.04f), glass, collider: false);
        Lit(Box(r, "chest_light", new Vector3(0, 1f, 0.247f), new Vector3(0.15f, 0.035f, 0.006f), status, collider: false));
        Cyl(r, "neck", new Vector3(0, 1.28f, 0), 0.11f, 0.12f, joint, collider: false);

        // Head (turns towards what the robot is working on)
        var head = Child(r, "Head", new Vector3(0, 1.33f, 0));
        Prim(PrimitiveType.Sphere, head, "head", new Vector3(0, 0.15f, 0), new Vector3(0.42f, 0.34f, 0.38f), shell, collider: false);
        Prim(PrimitiveType.Sphere, head, "visor", new Vector3(0, 0.16f, 0.06f), new Vector3(0.36f, 0.2f, 0.3f), glass, collider: false);
        foreach (int side in new[] { -1, 1 })
        {
            Lit(Prim(PrimitiveType.Sphere, head, "eye", new Vector3(side * 0.075f, 0.17f, 0.2f), Vector3.one * 0.05f, status, collider: false));
            Cyl(head, "ear", new Vector3(side * 0.21f, 0.15f, 0), 0.11f, 0.04f, accent, new Vector3(0, 0, 90), collider: false);
        }
        Cyl(head, "antenna", new Vector3(0.1f, 0.37f, -0.05f), 0.015f, 0.14f, dark, collider: false);
        Lit(Prim(PrimitiveType.Sphere, head, "antenna_tip", new Vector3(0.1f, 0.45f, -0.05f), Vector3.one * 0.04f, status, collider: false));

        // Arms holding the carrying tray; the Hand point sits on top of the tray.
        foreach (int side in new[] { -1, 1 })
        {
            Vector3 shoulder = new Vector3(side * 0.27f, 1.12f, 0), elbow = new Vector3(side * 0.3f, 0.93f, 0.14f), wrist = new Vector3(side * 0.2f, 0.975f, 0.4f);
            Prim(PrimitiveType.Sphere, r, "shoulder", shoulder, Vector3.one * 0.14f, accent, collider: false);
            Link(r, "upper_arm", shoulder, elbow, 0.08f, shell);
            Prim(PrimitiveType.Sphere, r, "elbow", elbow, Vector3.one * 0.095f, joint, collider: false);
            Link(r, "forearm", elbow, wrist, 0.07f, shell);
            Box(r, "gripper", new Vector3(side * 0.19f, 0.985f, 0.44f), new Vector3(0.03f, 0.06f, 0.14f), dark, collider: false);
            Box(r, "tray_rim", new Vector3(side * 0.18f, 0.995f, 0.45f), new Vector3(0.012f, 0.03f, 0.27f), front, collider: false);
        }
        Box(r, "tray", new Vector3(0, 0.985f, 0.45f), new Vector3(0.36f, 0.02f, 0.26f), dark, collider: false);
        Box(r, "tray_front", new Vector3(0, 0.995f, 0.585f), new Vector3(0.372f, 0.03f, 0.012f), front, collider: false);

        vis.head = head;
        vis.statusLights = lights.ToArray();
    }

    // Connects the robot's awareness overlays (path, view cone, target ring, pings) to the cameras that show them.
    void WireVisuals(GameObject robot, Camera eye, Camera top)
    {
        var vis = robot.GetComponent<RobotVisuals>();
        var mainGo = GameObject.FindWithTag("MainCamera");
        vis.audience = mainGo ? mainGo.GetComponent<Camera>() : null;
        vis.eye = eye;
        vis.nav = robot.GetComponent<UnityEngine.AI.NavMeshAgent>();
        vis.perception = robot.GetComponent<Perception>();
        vis.overlayLayer = AudienceOnlyLayer;
        vis.pathMaterial = Overlay("path", new Color(0.25f, 0.85f, 1f, 0.9f));
        vis.coneMaterial = Overlay("view_cone", new Color(0.2f, 0.75f, 1f, 0.16f));
        vis.ringMaterial = Overlay("target_ring", new Color(1f, 0.82f, 0.2f, 0.95f));
        vis.pingMaterial = Overlay("seen_ping", new Color(0.45f, 1f, 0.6f, 0.9f));
        vis.ringMesh = Ring(0.5f, 0.02f);
        foreach (var cam in new[] { eye, top }) cam.cullingMask &= ~(1 << AudienceOnlyLayer);
    }
}
