// Interactable.cs — put this on every object the VLM may refer to
// (balls, lockers, racks, doors...). Its id is the name the VLM uses.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;

public class Interactable : MonoBehaviour
{
    public static readonly List<Interactable> All = new List<Interactable>();

    [Tooltip("Name the VLM uses. Make it descriptive: red_water_bottle, locker_3, ball_rack_top")]
    public string id;
    [Tooltip("What sort of thing this is, shown to the VLM: ball, locker, shelf, door...")]
    public string kind;

    [Header("Pickable objects")]
    public bool pickable;

    [Header("Surfaces / containers")]
    public bool canHold;
    public Transform placePoint;                          // centre of the area where held objects are put
    public Vector2 slotArea = new Vector2(0.4f, 0.4f);    // usable width (x) and depth (z) around placePoint
    public float slotSpacing = 0.4f;
    public List<Interactable> levels = new List<Interactable>();  // shelf levels; placing on the parent fills the first free one
    public Interactable partOf;                           // the rack or cabinet a shelf level belongs to
    public List<Interactable> contents = new List<Interactable>();

    [Header("Where the robot stands")]
    public Transform approachPoint;
    public Transform approachPointBack;                   // doors: the other side

    [Header("Openable (locker, cabinet, door)")]
    public bool openable;
    public Transform door;                                // hinge pivot of the door
    public Vector3 openRotation = new Vector3(0, -100, 0);
    public float openTime = 0.8f;
    public bool startsOpen;
    [Tooltip("Glass doors: the contents can be seen while it is closed.")]
    public bool seeThrough;
    public bool locked;
    public string keyId;
    public Transform lockPoint;

    [SerializeField] Interactable containedIn;
    [SerializeField] int slotIndex = -1;

    public bool IsOpen { get; private set; }
    public Interactable ContainedIn => containedIn;
    public Interactable Enclosure => openable ? this : (partOf && partOf.openable ? partOf : null);
    public bool IsShut => Enclosure && !Enclosure.IsOpen;

    Quaternion closedRot;
    Collider[] cols;

    void Awake()
    {
        if (string.IsNullOrEmpty(id)) id = name.ToLower().Replace(' ', '_');
        cols = GetComponentsInChildren<Collider>();
        if (door)
        {
            closedRot = door.localRotation;
            if (startsOpen) door.localRotation = closedRot * Quaternion.Euler(openRotation);
        }
        IsOpen = startsOpen;
    }

    void OnEnable() => All.Add(this);
    void OnDisable() => All.Remove(this);

    public static Interactable Find(string id) =>
        All.Find(i => string.Equals(i.id, id, StringComparison.OrdinalIgnoreCase));

    public static string[] AllIds() => All.Select(i => i.id).ToArray();

    public Vector3 ApproachFrom(Vector3 from)
    {
        if (containedIn) return containedIn.ApproachFrom(from);
        if (approachPoint && approachPointBack)
            return FlatDistance(approachPoint.position, from) <= FlatDistance(approachPointBack.position, from)
                ? approachPoint.position : approachPointBack.position;
        if (approachPoint) return approachPoint.position;
        Vector3 dir = from - transform.position; dir.y = 0;
        if (dir.sqrMagnitude < 0.01f) dir = Vector3.back;
        return transform.position + dir.normalized * 0.7f;
    }

    public Interactable PlaceTarget =>
        levels.Count == 0 ? this : levels.FirstOrDefault(l => l.FreeSlot() >= 0) ?? levels[0];

    int Cols => Mathf.Max(1, Mathf.FloorToInt(slotArea.x / slotSpacing + 0.001f));
    int Rows => Mathf.Max(1, Mathf.FloorToInt(slotArea.y / slotSpacing + 0.001f));
    public int Capacity => canHold ? Cols * Rows : 0;

    // Spreads things out like a person would: the first item goes in the middle, each next one in the biggest gap.
    public int FreeSlot()
    {
        var taken = contents.Where(c => c).Select(c => SlotLocal(c.slotIndex)).ToList();
        int best = -1;
        float bestScore = float.MinValue;
        for (int i = 0; i < Capacity; i++)
        {
            if (contents.Any(c => c && c.slotIndex == i)) continue;
            var p = SlotLocal(i);
            float score = taken.Count == 0 ? -p.sqrMagnitude : taken.Min(t => (t - p).sqrMagnitude);
            if (score > bestScore + 1e-4f) { best = i; bestScore = score; }
        }
        return best;
    }

    Vector3 SlotLocal(int i)
    {
        int col = i % Cols, row = i / Cols % Rows;
        return new Vector3((col + 0.5f) * slotArea.x / Cols - slotArea.x / 2, 0,
                           (row + 0.5f) * slotArea.y / Rows - slotArea.y / 2);
    }

    public Vector3 SlotPosition(int i)
    {
        if (!placePoint)
        {
            var r = GetComponentInChildren<Renderer>();
            return r ? new Vector3(r.bounds.center.x, r.bounds.max.y, r.bounds.center.z) : transform.position + Vector3.up;
        }
        return placePoint.position + placePoint.rotation * SlotLocal(i);
    }

    public void AttachTo(Transform hand)
    {
        SetColliders(false);
        Leave();
        transform.SetParent(hand, false);
        transform.localPosition = Vector3.zero;
        transform.localRotation = Quaternion.identity;
    }

    // Returns false when the surface has no free slot.
    public bool PutOn(Interactable surface)
    {
        int slot = surface.FreeSlot();
        if (slot < 0) return false;
        Leave();
        transform.SetParent(surface.transform, true);
        transform.SetPositionAndRotation(surface.SlotPosition(slot),
            (surface.placePoint ? surface.placePoint : surface.transform).rotation);
        containedIn = surface;
        slotIndex = slot;
        surface.contents.Add(this);
        SetColliders(true);
        return true;
    }

    // The key stays in the lock for good and the robot's hand is free again.
    public void InsertInLock(Interactable target)
    {
        Leave();
        pickable = false;
        transform.SetParent(target.lockPoint ? target.lockPoint : target.transform, false);
        transform.localPosition = Vector3.zero;
        transform.localRotation = Quaternion.identity;
        target.locked = false;
    }

    void Leave()
    {
        if (containedIn) containedIn.contents.Remove(this);
        containedIn = null;
        slotIndex = -1;
    }

    void SetColliders(bool on)
    {
        if (cols == null) cols = GetComponentsInChildren<Collider>();
        foreach (var c in cols) c.enabled = on;
    }

    // One line of state for the VLM, e.g. "locker_5 (locker; closed; locked, needs locker_key)".
    // Only contents the robot knows about are listed, and only claims "empty" once it has looked up close.
    // "where" says which part of the building a free-standing thing is in, e.g. "on the floor, basketball court".
    public string Describe(Func<Interactable, bool> known = null, Func<Interactable, bool> checkedUpClose = null, string where = null)
    {
        var parts = new List<string>();
        if (!string.IsNullOrEmpty(kind)) parts.Add(kind);
        if (partOf) parts.Add($"part of {partOf.id}");
        if (openable) parts.Add(IsOpen ? "open" : "closed");
        if (locked) parts.Add($"locked, needs {keyId}");
        if (containedIn) parts.Add($"{(containedIn.Enclosure ? "in" : "on")} {containedIn.id}");
        else if (!string.IsNullOrEmpty(where)) parts.Add(where);
        if (canHold && ShowsContents)
        {
            var seen = contents.Where(c => c && (known == null || known(c))).Select(c => c.id).ToList();
            bool sure = checkedUpClose == null || checkedUpClose(this);
            if (seen.Count > 0) parts.Add("holds " + string.Join(", ", seen) + (sure ? "" : " (maybe more, not checked up close)"));
            else parts.Add(sure ? "empty" : "not checked up close");
        }
        return parts.Count == 0 ? id : $"{id} ({string.Join("; ", parts)})";
    }

    public IEnumerator SetOpen(bool open)
    {
        if (!door || IsOpen == open) { IsOpen = open; yield break; }
        Quaternion from = door.localRotation;
        Quaternion to = open ? closedRot * Quaternion.Euler(openRotation) : closedRot;
        for (float t = 0; t < 1f; t += Time.deltaTime / openTime)
        {
            door.localRotation = Quaternion.Slerp(from, to, Mathf.SmoothStep(0, 1, t));
            yield return null;
        }
        door.localRotation = to;
        IsOpen = open;
    }

    // Shelves belong to a cabinet, so a closed glass cabinet still shows what is on its shelves.
    public bool ShowsContents => !IsShut || (Enclosure && Enclosure.seeThrough);

    static float FlatDistance(Vector3 a, Vector3 b)
    {
        a.y = b.y = 0;
        return Vector3.Distance(a, b);
    }
}
