// RoomArea.cs — one room of the floor plan. The robot always knows the floor plan (rooms and the doors
// between them) but has to go and look to learn what is inside.
using System.Collections.Generic;
using UnityEngine;

public class RoomArea : MonoBehaviour
{
    public static readonly List<RoomArea> All = new List<RoomArea>();

    public Rect rect;                                   // x, z, width, depth in metres
    public bool roofed;                                 // hidden from the overhead camera
    public List<Interactable> doors = new List<Interactable>();

    void OnEnable() => All.Add(this);
    void OnDisable() => All.Remove(this);

    public string Id => TryGetComponent<Interactable>(out var it) ? it.id : name;
    public bool Contains(Vector3 p) => rect.Contains(new Vector2(p.x, p.z));
    public static RoomArea At(Vector3 p) => All.Find(r => r.Contains(p));
}
