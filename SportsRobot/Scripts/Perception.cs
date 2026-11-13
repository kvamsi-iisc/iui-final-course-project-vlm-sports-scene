// Perception.cs — what the robot knows. An object becomes known once it is actually visible in the
// robot's eye camera or the overhead camera: inside the picture, not blocked by anything, and big
// enough to make out in the image the VLM receives. Rooms and doors (the floor plan) are always known.
using System.Collections.Generic;
using System.Linq;
using UnityEngine;

public class Perception : MonoBehaviour
{
    public Camera eye, overhead;
    [Tooltip("An object must cover at least this many pixels of the image sent to the VLM.")]
    public float minPixels = 4f;
    [Tooltip("Height of the images sent to the VLM; used to work out how big things look.")]
    public int imageHeight = 480;
    [Tooltip("Shelves, carts and opened lockers count as checked once the eye has seen them from this close.")]
    public float inspectRange = 4f;
    public float scanInterval = 0.2f;

    public event System.Action<Interactable> Seen;   // raised the moment something is noticed for the first time

    readonly HashSet<Interactable> seen = new HashSet<Interactable>();
    readonly HashSet<Interactable> inspected = new HashSet<Interactable>();
    readonly HashSet<RoomArea> visited = new HashSet<RoomArea>();
    readonly List<Interactable> fresh = new List<Interactable>();
    readonly List<Renderer> renderers = new List<Renderer>();
    readonly Vector3[] samples = new Vector3[2];
    float nextScan;
    const int SeeThroughLayer = 2, RobotLayer = 4;   // glass and nets; the robot's own body

    void Start() => Scan();

    void Update()
    {
        if (Time.time >= nextScan) Scan();
    }

    // The floor plan is always known: rooms and the doors between them.
    static bool OnMap(Interactable i) => i.kind == "room" || i.kind == "door";

    public bool Knows(Interactable i) => i && (OnMap(i) || seen.Contains(i) || (i.partOf && seen.Contains(i.partOf)));
    public IEnumerable<Interactable> Known => Interactable.All.Where(Knows);

    public bool Visited(RoomArea r) => visited.Contains(r);
    public IEnumerable<RoomArea> UnvisitedRooms => RoomArea.All.Where(r => !visited.Contains(r));

    // A shelf, cart or locker whose contents have been looked at from close by (lockers only while open).
    public bool Inspected(Interactable i) => i && inspected.Contains(i.partOf ? i.partOf : i);

    // Known places that might hold things the robot hasn't spotted yet.
    public IEnumerable<Interactable> Unchecked => Known.Where(i => !i.partOf && IsHolder(i) && !inspected.Contains(i));

    public static bool IsHolder(Interactable i) => i.kind != "room" && i.kind != "door" && (i.canHold || i.levels.Count > 0);

    public List<string> TakeNewlySeen()
    {
        var ids = fresh.Where(i => i).Select(i => i.id).ToList();
        fresh.Clear();
        return ids;
    }

    public void Scan()
    {
        nextScan = Time.time + scanInterval;
        var room = RoomArea.At(transform.position);
        if (room) visited.Add(room);

        foreach (var it in Interactable.All)
        {
            if (OnMap(it) || it.partOf) continue;
            if (!seen.Contains(it) && (Visible(eye, it) || Visible(overhead, it)))
            {
                seen.Add(it);
                fresh.Add(it);
                Seen?.Invoke(it);
            }
            if (eye && seen.Contains(it) && IsHolder(it) && !inspected.Contains(it) && CanCheckInside(it)
                && Vector3.Distance(eye.transform.position, it.transform.position) <= inspectRange && Visible(eye, it))
                inspected.Add(it);
        }
    }

    // The robot has just stood in front of this place and looked into it: count it as checked even when its
    // middle is outside the picture because the eye is tilted down at the floor of a locker.
    public void MarkInspected(Interactable i)
    {
        if (i && CanCheckInside(i)) inspected.Add(i.partOf ? i.partOf : i);
    }

    static bool CanCheckInside(Interactable i) => !i.openable || i.IsOpen || i.seeThrough;

    bool Visible(Camera cam, Interactable it)
    {
        if (!cam || !TryBounds(it, out var b)) return false;
        float focal = imageHeight * 0.5f / Mathf.Tan(cam.fieldOfView * 0.5f * Mathf.Deg2Rad);
        float size = Mathf.Max(b.size.x, b.size.y, b.size.z);
        Vector3 from = cam.transform.position;
        int mask = ~((1 << SeeThroughLayer) | (1 << RobotLayer));
        samples[0] = b.center;
        samples[1] = b.center + Vector3.up * (b.extents.y * 0.6f);
        foreach (var p in samples)
        {
            var vp = cam.WorldToViewportPoint(p);
            if (vp.z <= cam.nearClipPlane || vp.x < 0 || vp.x > 1 || vp.y < 0 || vp.y > 1) continue;
            if (size * focal / vp.z < minPixels) return false;
            Vector3 d = p - from;
            float dist = d.magnitude;
            if (!Physics.Raycast(from, d / dist, out var hit, dist - 0.02f, mask, QueryTriggerInteraction.Ignore)) return true;
            var h = hit.collider.GetComponentInParent<Interactable>();
            if (h && (h == it || h.partOf == it || (h.ContainedIn && (h.ContainedIn == it || h.ContainedIn.partOf == it)))) return true;
        }
        return false;
    }

    bool TryBounds(Interactable it, out Bounds b)
    {
        it.GetComponentsInChildren(false, renderers);
        b = default;
        bool any = false;
        foreach (var r in renderers)
        {
            if (!r.enabled) continue;
            if (!any) { b = r.bounds; any = true; }
            else b.Encapsulate(r.bounds);
        }
        return any;
    }
}
