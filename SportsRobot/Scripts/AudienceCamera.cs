// AudienceCamera.cs — the viewers' camera. Looks down from the south (north is up, like the overhead map)
// from just far enough to fit the whole building, and refits itself whenever the Game window changes shape.
using UnityEngine;

[RequireComponent(typeof(Camera))]
public class AudienceCamera : MonoBehaviour
{
    public Rect building;                 // x, z, width, depth in metres
    public float wallHeight = 3.5f;
    public float pitch = 54f;
    [Range(0.5f, 1f)] public float fill = 0.94f;   // how much of the picture the building may take up

    Camera cam;
    float fittedAspect;

    void Start() => Fit(GetComponent<Camera>().aspect);

    void LateUpdate()
    {
        if (!cam) cam = GetComponent<Camera>();
        if (Mathf.Abs(cam.aspect - fittedAspect) > 0.01f) Fit(cam.aspect);
    }

    public void Fit(float aspect)
    {
        if (!cam) cam = GetComponent<Camera>();
        fittedAspect = aspect;
        var t = transform;
        var center = new Vector3(building.center.x, 0, building.center.y);
        t.rotation = Quaternion.Euler(pitch, 0, 0);
        float tanV = Mathf.Tan(cam.fieldOfView * 0.5f * Mathf.Deg2Rad), tanH = tanV * aspect;
        for (float dist = 8f; dist < 400f; dist += 0.5f)
        {
            t.position = center - t.forward * dist;
            if (Fits(tanH, tanV)) break;
        }
    }

    bool Fits(float tanH, float tanV)
    {
        for (int i = 0; i < 8; i++)
        {
            var corner = new Vector3((i & 1) == 0 ? building.xMin : building.xMax, (i & 2) == 0 ? 0f : wallHeight,
                                     (i & 4) == 0 ? building.yMin : building.yMax);
            var local = transform.InverseTransformPoint(corner);
            if (local.z <= 0 || Mathf.Abs(local.x) > local.z * tanH * fill || Mathf.Abs(local.y) > local.z * tanV * fill) return false;
        }
        return true;
    }
}
