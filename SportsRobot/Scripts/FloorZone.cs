// FloorZone.cs — a named, painted area of a room's floor (basketball court, gym, equipment storage).
// It only adds words to what the robot reports about where things are; it can't be a target itself.
using System.Collections.Generic;
using UnityEngine;

public class FloorZone : MonoBehaviour
{
    public static readonly List<FloorZone> All = new List<FloorZone>();

    public string label;
    public Rect rect;                                   // x, z, width, depth in metres

    void OnEnable() => All.Add(this);
    void OnDisable() => All.Remove(this);

    public static FloorZone At(Vector3 p) => All.Find(z => z.rect.Contains(new Vector2(p.x, p.z)));

    // "the gym", or "" when the point is on plain floor.
    public static string Name(Vector3 p)
    {
        var z = At(p);
        return z ? z.label : "";
    }
}
