// FloorZone.cs — a named, painted area of a room's floor (basketball court, gym, equipment storage).
// The robot reports where things are with it, and it is a place of its own: the robot can walk to it
// (move_to gym) and search the furniture standing in it (explore equipment_storage).
using System.Collections.Generic;
using UnityEngine;

public class FloorZone : MonoBehaviour
{
    public static readonly List<FloorZone> All = new List<FloorZone>();

    public string label;
    public Rect rect;                                   // x, z, width, depth in metres

    public string Id => label.Trim().ToLowerInvariant().Replace(' ', '_');

    // Zones are part of the floor plan, like rooms: always known, kind "area". Added here rather than by the
    // scene builder so that scenes built earlier get them too.
    void Awake()
    {
        if (GetComponent<Interactable>()) return;
        var it = gameObject.AddComponent<Interactable>();
        it.id = Id;
        it.kind = "area";
        it.approachPoint = transform;
    }

    void OnEnable() => All.Add(this);
    void OnDisable() => All.Remove(this);

    public static FloorZone At(Vector3 p) => All.Find(z => z.rect.Contains(new Vector2(p.x, p.z)));

    // "the gym", or "" when the point is on plain floor.
    public static string Name(Vector3 p)
    {
        var z = At(p);
        return z ? z.Id : "";
    }
}
