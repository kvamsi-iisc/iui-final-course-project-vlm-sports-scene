// RobotAgent.cs — the "body". Runs one validated action at a time and reports what happened.
// Needs a NavMeshAgent on the robot and a baked NavMesh in the scene.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;
using UnityEngine.AI;

[RequireComponent(typeof(NavMeshAgent))]
public class RobotAgent : MonoBehaviour
{
    public Transform hand;                 // empty child where held objects attach
    public Transform eye;                  // eye camera; tilts to look at what the robot works on
    public Perception perception;
    public float reachDistance = 2.0f;     // how close the robot must be to interact
    public float turnSpeed = 180f;         // deg/sec
    public float moveTimeout = 30f;

    NavMeshAgent nav;
    NavMeshPath path;
    Interactable held;
    Quaternion eyeRest;

    public Interactable Held => held;
    public string HeldName => held ? held.id : "";

    void Awake()
    {
        nav = GetComponent<NavMeshAgent>();
        path = new NavMeshPath();
        if (!perception) perception = GetComponent<Perception>();
        if (eye) eyeRest = eye.localRotation;
    }

    // Runs one action, then reports (error or null, note for the VLM or null).
    public IEnumerator RunOne(RobotAction a, Action<string, string> done)
    {
        string error = null, note = null;
        yield return Run(a, e => error ??= e, n => note = n);
        if (perception) perception.Scan();
        done(error, note);
    }

    // Each case maps one command word to engine behaviour.
    IEnumerator Run(RobotAction a, Action<string> fail, Action<string> note)
    {
        string err = null;
        void Fail(string e) { err ??= e; fail(e); }

        Interactable t = string.IsNullOrEmpty(a.target) ? null : Interactable.Find(a.target);
        bool needsTarget = a.action != "rotate" && a.action != "wait" && a.action != "look_around" && a.action != "explore";
        if (needsTarget && t == null) { Fail($"no object '{a.target}'"); yield break; }

        switch (a.action)
        {
            case "move_to":
                yield return MoveTo(t, Fail);
                break;

            case "pick":
                if (held) { Fail($"already holding {held.id}; put it down first (place it on a bench, shelf or mat nearby), then pick this up"); yield break; }
                if (!t.pickable) { Fail($"{t.id} can't be picked up"); yield break; }
                if (t.ContainedIn && t.ContainedIn.IsShut) { Fail($"{t.ContainedIn.Enclosure.id} is closed"); yield break; }
                yield return EnsureNear(t, Fail);
                if (err != null) break;
                AimEye(t.transform.position);
                t.AttachTo(hand);
                held = t;
                yield return new WaitForSeconds(0.3f);
                break;

            case "place":
                if (!held) { Fail("not holding anything"); yield break; }
                var spot = t.PlaceTarget;
                if (!spot.canHold) { Fail($"can't put things on {t.id}"); yield break; }
                if (spot.IsShut) { Fail($"{spot.Enclosure.id} is closed"); yield break; }
                if (spot.FreeSlot() < 0) { Fail($"{spot.id} is full"); yield break; }
                yield return EnsureNear(spot, Fail);
                if (err != null) break;
                var item = held;
                item.PutOn(spot);
                held = null;
                AimEye(item.transform.position);
                if (spot != t) note($"put {item.id} on {spot.id}");
                yield return new WaitForSeconds(0.3f);
                break;

            case "open":
            case "close":
                yield return OpenClose(t, a.action == "open", Fail);
                break;

            case "rotate":
                yield return Rotate(a.degrees);
                break;

            case "look_around":
                yield return LookAround();
                break;

            case "explore":
                yield return Explore(t, Fail, note);
                break;

            case "wait":
                yield return new WaitForSeconds(Mathf.Clamp(a.seconds, 0f, 10f));
                break;

            default:
                Fail($"unsupported action '{a.action}'");
                break;
        }
    }

    IEnumerator OpenClose(Interactable t, bool open, Action<string> fail)
    {
        if (!t.openable) { fail($"{t.id} can't be opened"); yield break; }
        bool hasKey = held && held.id == t.keyId;
        if (open && t.locked && !hasKey) { fail($"{t.id} is locked; it needs {t.keyId}"); yield break; }
        bool failed = false;
        // Lockers and cabinets are opened from straight in front, so the robot ends up looking into them
        // rather than past the open door of the locker next to it.
        if (t.kind == "door") yield return EnsureNear(t, e => { failed = true; fail(e); });
        else yield return EnsureInFront(t, e => { failed = true; fail(e); });
        if (failed) yield break;
        if (open && t.locked) { held.InsertInLock(t); held = null; }
        yield return t.SetOpen(open);
        if (t.kind == "door") yield return new WaitForSeconds(0.2f);   // let the NavMesh re-carve the doorway
        else AimEye(t.placePoint ? t.placePoint.position : Middle(t));
    }

    // ---------- exploring ----------

    // One search step: look inside the target (a room or a piece of furniture), or the nearest place the
    // robot hasn't checked yet. Rooms are entered (opening a door if needed) and looked around in.
    IEnumerator Explore(Interactable target, Action<string> fail, Action<string> note)
    {
        if (!perception) { fail("this robot has no perception component"); yield break; }
        bool failed = false;
        void Fail(string e) { failed = true; fail(e); }

        var targetRoom = target && target.kind == "room" ? target.GetComponent<RoomArea>() : null;
        var targetZone = target && target.kind == "area" ? target.GetComponent<FloorZone>() : null;
        if (targetZone)
        {
            // A floor area: look closely at the nearest piece of furniture in it that hasn't been checked yet.
            var inZone = Nearest(perception.Unchecked.Where(i => Searchable(i) && FloorZone.At(i.transform.position) == targetZone));
            if (!inZone) { Fail($"nothing left to explore in {target.id}"); yield break; }
            yield return Inspect(inZone, Fail, note);
            yield break;
        }
        if (target && !targetRoom)
        {
            if (!Perception.IsHolder(target)) { Fail($"{target.id} has nothing to look inside; explore a room or a shelf, locker or cart"); yield break; }
            yield return Inspect(target, Fail, note);
            yield break;
        }

        var here = RoomArea.At(transform.position);
        var scope = targetRoom ? targetRoom : here;
        if (targetRoom && !perception.Visited(targetRoom))
        {
            yield return EnterRoom(targetRoom, Fail, note);
            yield break;
        }

        var spot = Nearest(perception.Unchecked.Where(i => Searchable(i) && RoomArea.At(i.transform.position) == scope));
        if (!spot && !targetRoom)
        {
            var room = perception.UnvisitedRooms.OrderBy(r => FlatDistance(r.transform.position)).FirstOrDefault();
            if (room) { yield return EnterRoom(room, Fail, note); yield break; }
            spot = Nearest(perception.Unchecked.Where(Searchable));
        }
        if (!spot)
        {
            var locked = perception.Known.Where(i => i.locked && (!targetRoom || RoomArea.At(i.transform.position) == targetRoom))
                                         .Select(i => $"{i.id} is locked and needs {i.keyId}").ToList();
            Fail($"nothing left to explore{(targetRoom ? " in " + targetRoom.Id : "")}" +
                 (locked.Count > 0 ? "; " + string.Join(", ", locked) : ""));
            yield break;
        }
        yield return Inspect(spot, Fail, note);
        if (!failed && !perception.Inspected(spot)) note($"went to {spot.id} but couldn't get a clear look inside");
    }

    static bool Searchable(Interactable i) => !(i.locked && i.openable && !i.IsOpen);

    IEnumerator Inspect(Interactable spot, Action<string> fail, Action<string> note)
    {
        bool failed = false;
        if (spot.openable && !spot.IsOpen && !spot.seeThrough)
        {
            yield return OpenClose(spot, true, e => { failed = true; fail(e); });
            if (!failed)
            {
                note($"opened {spot.id} to look inside");
                foreach (var level in spot.levels)
                {
                    AimEye(level.placePoint ? level.placePoint.position : Middle(level));
                    yield return new WaitForSeconds(0.3f);
                    if (perception) perception.Scan();
                }
                if (perception) { perception.Scan(); perception.MarkInspected(spot); }
            }
            yield break;
        }
        yield return EnsureInFront(spot, e => { failed = true; fail(e); });
        if (failed) yield break;
        // A rack is looked at shelf by shelf, so that nothing on a low or high shelf is missed.
        foreach (var level in spot.levels.Count > 0 ? spot.levels : new List<Interactable> { spot })
        {
            AimEye(level.placePoint ? level.placePoint.position : Middle(level));
            yield return new WaitForSeconds(0.3f);
            if (perception) perception.Scan();
        }
        if (perception) perception.MarkInspected(spot);
        note($"looked closely at {spot.id}");
    }

    IEnumerator EnterRoom(RoomArea room, Action<string> fail, Action<string> note)
    {
        var roomIt = room.GetComponent<Interactable>();
        if (!Reachable(roomIt))
        {
            var door = room.doors.Where(d => !d.IsOpen).OrderBy(d => FlatDistance(d.transform.position)).FirstOrDefault();
            if (!door) { fail($"can't find a way into {room.Id}"); yield break; }
            bool failed = false;
            yield return OpenClose(door, true, e => { failed = true; fail(e); });
            if (failed) yield break;
            note($"opened {door.id}; {room.Id} is reachable now");
            yield break;
        }
        bool moveFailed = false;
        yield return MoveTo(roomIt, e => { moveFailed = true; fail(e); });
        if (moveFailed) yield break;
        yield return LookAround();
        note($"walked into {room.Id} and looked around");
    }

    bool Reachable(Interactable t) =>
        NavMesh.SamplePosition(Goal(t), out var hit, 1.5f, NavMesh.AllAreas)
        && nav.CalculatePath(hit.position, path) && path.status == NavMeshPathStatus.PathComplete;

    // Where to stand for t. A door has a spot on each side: take the one the robot can actually walk to
    // (the nearest in a straight line may be behind the closed door itself), preferring the shorter walk.
    Vector3 Goal(Interactable t)
    {
        Vector3 best = t.ApproachFrom(transform.position);
        if (t.ContainedIn || !t.approachPoint || !t.approachPointBack) return best;
        float bestLength = float.MaxValue;
        foreach (var side in new[] { t.approachPoint.position, t.approachPointBack.position })
        {
            if (!NavMesh.SamplePosition(side, out var hit, 1.5f, NavMesh.AllAreas) ||
                !nav.CalculatePath(hit.position, path) || path.status != NavMeshPathStatus.PathComplete) continue;
            float length = 0f;
            var corners = path.corners;
            for (int i = 1; i < corners.Length; i++) length += Vector3.Distance(corners[i - 1], corners[i]);
            if (length < bestLength) { bestLength = length; best = side; }
        }
        return best;
    }

    Interactable Nearest(IEnumerable<Interactable> options) =>
        options.OrderBy(i => FlatDistance(i.ApproachFrom(transform.position))).FirstOrDefault();

    IEnumerator LookAround()
    {
        ResetEye();
        for (int i = 0; i < 4; i++)
        {
            yield return Rotate(90);
            if (perception) perception.Scan();
            yield return new WaitForSeconds(0.25f);
        }
    }

    // ---------- moving and turning ----------

    IEnumerator MoveTo(Interactable t, Action<string> fail)
    {
        ResetEye();
        Vector3 goal = Goal(t);
        if (!NavMesh.SamplePosition(goal, out var hit, 1.5f, NavMesh.AllAreas) ||
            !nav.CalculatePath(hit.position, path) || path.status != NavMeshPathStatus.PathComplete)
        {
            fail($"no path to {t.id} (is a door closed?)");
            yield break;
        }
        nav.isStopped = false;
        nav.SetDestination(hit.position);
        float timer = 0f;
        while (nav.pathPending || nav.remainingDistance > nav.stoppingDistance + 0.05f)
        {
            timer += Time.deltaTime;
            if (timer > moveTimeout) { nav.isStopped = true; fail($"couldn't reach {t.id}"); yield break; }
            yield return null;
        }
        if (t.kind != "room" && t.kind != "area") yield return Face(t.transform.position);
    }

    // Small robustness win: if the VLM forgot a move_to, walk there anyway.
    IEnumerator EnsureNear(Interactable t, Action<string> fail)
    {
        bool near = FlatDistance(t.transform.position) <= reachDistance
                 || FlatDistance(Goal(t)) <= 0.6f;
        if (near) yield return Face(t.transform.position);
        else yield return MoveTo(t, fail);
    }

    // Stricter than EnsureNear: stand on the object's own approach spot, facing it.
    IEnumerator EnsureInFront(Interactable t, Action<string> fail)
    {
        if (FlatDistance(Goal(t)) <= 0.45f) yield return Face(t.transform.position);
        else yield return MoveTo(t, fail);
    }

    IEnumerator Face(Vector3 point)
    {
        Vector3 dir = point - transform.position; dir.y = 0;
        if (dir.sqrMagnitude < 0.001f) yield break;
        Quaternion goal = Quaternion.LookRotation(dir);
        while (Quaternion.Angle(transform.rotation, goal) > 1f)
        {
            transform.rotation = Quaternion.RotateTowards(transform.rotation, goal, turnSpeed * Time.deltaTime);
            yield return null;
        }
    }

    IEnumerator Rotate(float degrees)
    {
        ResetEye();
        float left = Mathf.Abs(degrees), sign = Mathf.Sign(degrees);
        while (left > 0f)
        {
            float step = Mathf.Min(left, turnSpeed * Time.deltaTime);
            transform.Rotate(0, sign * step, 0);
            left -= step;
            yield return null;
        }
    }

    // Tilt the eye down (or up) at what the robot is working on, so the next picture shows the result.
    void AimEye(Vector3 point)
    {
        if (!eye) return;
        Vector3 local = transform.InverseTransformPoint(point) - eye.localPosition;
        float pitch = Mathf.Atan2(-local.y, Mathf.Max(0.05f, new Vector2(local.x, local.z).magnitude)) * Mathf.Rad2Deg;
        eye.localRotation = Quaternion.Euler(Mathf.Clamp(pitch, -10f, 65f), 0, 0);
    }

    void ResetEye()
    {
        if (eye) eye.localRotation = eyeRest;
    }

    static Vector3 Middle(Interactable i)
    {
        var rs = i.GetComponentsInChildren<Renderer>();
        if (rs.Length == 0) return i.transform.position;
        var b = rs[0].bounds;
        foreach (var r in rs) b.Encapsulate(r.bounds);
        return b.center;
    }

    float FlatDistance(Vector3 p)
    {
        Vector3 d = p - transform.position; d.y = 0;
        return d.magnitude;
    }
}
