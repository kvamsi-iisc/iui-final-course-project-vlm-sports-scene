// RobotVisuals.cs — shows the audience what the robot is aware of: the route it is driving, the area
// its eye can see right now, the object it is working on, and a ping whenever it notices something new.
// Everything is drawn on a layer the robot's own cameras don't render, so the VLM never sees these hints.
using System.Collections.Generic;
using TMPro;
using UnityEngine;
using UnityEngine.AI;

public class RobotVisuals : MonoBehaviour
{
    public enum Mood { Idle, Thinking, Acting, Asking, Error }

    [Header("Wired by the scene builder")]
    public Renderer[] statusLights;
    public Transform head;
    public Camera eye, audience;
    public NavMeshAgent nav;
    public Perception perception;
    public Material pathMaterial, coneMaterial, ringMaterial, pingMaterial;
    public Mesh ringMesh;
    public int overlayLayer = 5;

    [Header("What to show")]
    public bool showPath = true;
    public bool showViewCone = true, showTarget = true, showPings = true, showCaption = true;
    public float viewRange = 6f;

    const int Rays = 40, MaxPings = 30;
    const int SightMask = ~((1 << 2) | (1 << 4));   // not glass or nets, not the robot itself

    class PingMark { public Transform t; public Material m; public float born; }

    readonly Vector3[] corners = new Vector3[64];
    readonly Vector3[] coneVerts = new Vector3[Rays + 2];
    readonly List<PingMark> pings = new List<PingMark>();
    readonly List<Renderer> scratch = new List<Renderer>();
    Material statusMat;
    Mood mood;
    Transform overlays, goalRing, targetRing, cone;
    LineRenderer line;
    Mesh coneMesh;
    TextMeshPro caption;
    Interactable target;
    float targetRadius = 0.5f;

    void Start()
    {
        if (statusLights != null && statusLights.Length > 0 && statusLights[0])
        {
            statusMat = new Material(statusLights[0].sharedMaterial);
            foreach (var r in statusLights) if (r) r.sharedMaterial = statusMat;
        }
        overlays = new GameObject("RobotOverlays").transform;

        line = Make("path").AddComponent<LineRenderer>();
        line.sharedMaterial = pathMaterial;
        line.widthMultiplier = 0.14f;
        line.numCornerVertices = 4;
        line.numCapVertices = 4;
        line.useWorldSpace = true;
        line.alignment = LineAlignment.TransformZ;
        line.transform.rotation = Quaternion.Euler(90, 0, 0);   // lie flat on the floor
        line.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        line.enabled = false;

        goalRing = Ring("goal", pathMaterial);
        targetRing = Ring("target", ringMaterial);

        cone = Make("view_cone").transform;
        coneMesh = new Mesh { name = "view_cone" };
        coneMesh.MarkDynamic();
        var tris = new int[Rays * 3];
        for (int i = 0; i < Rays; i++) { tris[i * 3] = 0; tris[i * 3 + 1] = i + 1; tris[i * 3 + 2] = i + 2; }
        coneMesh.vertices = coneVerts;
        coneMesh.triangles = tris;
        cone.gameObject.AddComponent<MeshFilter>().sharedMesh = coneMesh;
        Plain(cone.gameObject.AddComponent<MeshRenderer>(), coneMaterial);

        caption = Make("caption").AddComponent<TextMeshPro>();
        caption.fontSize = 6f;
        caption.alignment = TextAlignmentOptions.Center;
        caption.rectTransform.sizeDelta = new Vector2(14, 1.5f);
        caption.color = Color.white;
        caption.outlineWidth = 0.25f;
        caption.outlineColor = new Color32(10, 14, 20, 255);
        caption.text = "";

        if (perception) perception.Seen += OnSeen;
    }

    void OnDestroy()
    {
        if (perception) perception.Seen -= OnSeen;
        if (statusMat) Destroy(statusMat);
        foreach (var p in pings) if (p.m) Destroy(p.m);
    }

    // ---------- called by CommandClient ----------

    public void Show(Mood m, string text = "", Interactable workingOn = null)
    {
        mood = m;
        target = workingOn;
        if (caption) caption.text = text ?? "";
        if (!target) return;
        target.GetComponentsInChildren(false, scratch);
        targetRadius = 0.8f;
        if (scratch.Count > 0)
        {
            var b = scratch[0].bounds;
            foreach (var r in scratch) b.Encapsulate(r.bounds);
            targetRadius = Mathf.Clamp(Mathf.Max(b.extents.x, b.extents.z) * 1.25f, 0.16f, 1.8f);
        }
    }

    void OnSeen(Interactable it)
    {
        if (!showPings || !ringMesh || !it || pings.Count >= MaxPings) return;
        var t = Ring("seen_ping", null);
        var m = new Material(pingMaterial);
        t.GetComponent<MeshRenderer>().sharedMaterial = m;
        var p = it.transform.position;
        t.position = new Vector3(p.x, Mathf.Max(p.y, 0f) + 0.07f, p.z);
        t.gameObject.SetActive(true);
        pings.Add(new PingMark { t = t, m = m, born = Time.time });
    }

    // ---------- every frame ----------

    void LateUpdate()
    {
        Lights();
        Head();
        Path();
        ViewCone();
        Target();
        Pings();
        if (caption)
        {
            caption.enabled = showCaption && caption.text.Length > 0;
            caption.transform.position = transform.position + Vector3.up * 2.5f;
            if (audience) caption.transform.rotation = audience.transform.rotation;
        }
    }

    void Lights()
    {
        if (!statusMat) return;
        Color c; float pulse = 1f;
        switch (mood)
        {
            case Mood.Thinking: c = new Color(1f, 0.7f, 0.15f); pulse = 0.55f + 0.45f * Mathf.Sin(Time.time * 6f); break;
            case Mood.Acting: c = new Color(0.3f, 1f, 0.45f); break;
            case Mood.Asking: c = new Color(0.8f, 0.5f, 1f); pulse = 0.65f + 0.35f * Mathf.Sin(Time.time * 3f); break;
            case Mood.Error: c = new Color(1f, 0.25f, 0.2f); break;
            default: c = new Color(0.2f, 0.85f, 1f); break;
        }
        statusMat.color = c;
        statusMat.SetColor("_EmissionColor", c * (2.5f * pulse));
    }

    void Head()
    {
        if (!head) return;
        float yaw = 0f;
        if (target && target.transform != transform && !target.transform.IsChildOf(transform))
        {
            var local = transform.InverseTransformPoint(target.transform.position);
            yaw = Mathf.Clamp(Mathf.Atan2(local.x, local.z) * Mathf.Rad2Deg, -70f, 70f);
        }
        else if (mood == Mood.Thinking) yaw = Mathf.Sin(Time.time * 1.5f) * 25f;
        head.localRotation = Quaternion.Slerp(head.localRotation, Quaternion.Euler(0, yaw, 0), Time.deltaTime * 5f);
    }

    void Path()
    {
        bool moving = showPath && nav && nav.isOnNavMesh && nav.hasPath && !nav.pathPending && nav.remainingDistance > 0.35f;
        line.enabled = moving;
        goalRing.gameObject.SetActive(moving);
        if (!moving) return;
        int n = nav.path.GetCornersNonAlloc(corners);
        line.positionCount = n;
        for (int i = 0; i < n; i++) line.SetPosition(i, corners[i] + Vector3.up * 0.06f);
        if (n == 0) return;
        goalRing.position = corners[n - 1] + Vector3.up * 0.06f;
        goalRing.localScale = Vector3.one * (0.7f + 0.08f * Mathf.Sin(Time.time * 5f));
    }

    // The slice of floor the eye camera covers, cut short wherever a wall or tall furniture blocks the view.
    void ViewCone()
    {
        cone.gameObject.SetActive(showViewCone && eye);
        if (!cone.gameObject.activeSelf) return;
        cone.SetPositionAndRotation(transform.position + Vector3.up * 0.05f, Quaternion.Euler(0, transform.eulerAngles.y, 0));
        float half = Mathf.Atan(Mathf.Tan(eye.fieldOfView * 0.5f * Mathf.Deg2Rad) * eye.aspect);
        Vector3 from = transform.position + Vector3.up * 1.2f;
        coneVerts[0] = Vector3.zero;
        for (int i = 0; i <= Rays; i++)
        {
            float a = Mathf.Lerp(-half, half, i / (float)Rays);
            var dir = new Vector3(Mathf.Sin(a), 0, Mathf.Cos(a));
            float dist = Physics.Raycast(from, cone.rotation * dir, out var hit, viewRange, SightMask, QueryTriggerInteraction.Ignore)
                ? hit.distance : viewRange;
            coneVerts[i + 1] = dir * dist;
        }
        coneMesh.vertices = coneVerts;
        coneMesh.RecalculateBounds();
    }

    void Target()
    {
        bool on = showTarget && target && target.kind != "room";
        targetRing.gameObject.SetActive(on);
        if (!on) return;
        var p = target.transform.position;
        targetRing.position = new Vector3(p.x, (target.pickable ? Mathf.Max(p.y, 0f) : 0f) + 0.07f, p.z);
        targetRing.localScale = Vector3.one * (2f * targetRadius * (1f + 0.07f * Mathf.Sin(Time.time * 6f)));
    }

    void Pings()
    {
        for (int i = pings.Count - 1; i >= 0; i--)
        {
            var p = pings[i];
            float t = (Time.time - p.born) / 0.9f;
            if (t >= 1f || !p.t)
            {
                if (p.t) Destroy(p.t.gameObject);
                if (p.m) Destroy(p.m);
                pings.RemoveAt(i);
                continue;
            }
            p.t.localScale = Vector3.one * Mathf.Lerp(0.25f, 1.5f, t);
            var c = p.m.color; c.a = 1f - t;
            p.m.color = c;
        }
    }

    // ---------- helpers ----------

    GameObject Make(string name)
    {
        var g = new GameObject(name) { layer = overlayLayer };
        g.transform.SetParent(overlays, false);
        return g;
    }

    Transform Ring(string name, Material m)
    {
        var g = Make(name);
        g.AddComponent<MeshFilter>().sharedMesh = ringMesh;
        Plain(g.AddComponent<MeshRenderer>(), m);
        g.SetActive(false);
        return g.transform;
    }

    static void Plain(MeshRenderer r, Material m)
    {
        r.sharedMaterial = m;
        r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        r.receiveShadows = false;
    }
}
