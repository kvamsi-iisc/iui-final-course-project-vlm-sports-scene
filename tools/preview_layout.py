"""Preview a layout without Unity: renders the items, furniture close-ups and a floor plan,
using the same shapes, sizes and shelf slots as SportsRobot/Editor/SportsRoomBuilder.cs.

    python3 tools/preview_layout.py SportsRobot/Layouts/sports_room.json previews
"""
import json, math, sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from matplotlib.colors import to_rgba

LAYOUT = sys.argv[1]
OUT = sys.argv[2]

# ---------------- math / scene graph (Unity conventions) ----------------
def euler(x=0, y=0, z=0):
    x, y, z = np.radians([x, y, z])
    Rx = np.array([[1, 0, 0], [0, math.cos(x), -math.sin(x)], [0, math.sin(x), math.cos(x)]])
    Ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    Rz = np.array([[math.cos(z), -math.sin(z), 0], [math.sin(z), math.cos(z), 0], [0, 0, 1]])
    return Ry @ Rx @ Rz

def from_to(a, b):
    a = np.array(a, float) / np.linalg.norm(a); b = np.array(b, float) / np.linalg.norm(b)
    v = np.cross(a, b); c = np.dot(a, b)
    if c < -0.9999:
        return np.diag([1, -1, -1]) if abs(a[0]) < 0.9 else np.diag([-1, -1, 1])
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx / (1 + c)

class Node:
    def __init__(self, parent=None, pos=(0, 0, 0), rot=None, scale=(1, 1, 1)):
        M = np.eye(4)
        R = np.eye(3) if rot is None else rot
        s = np.array(scale, float) if np.ndim(scale) else np.array([scale] * 3, float)
        M[:3, :3] = R @ np.diag(s)
        M[:3, 3] = pos
        self.M = parent.M @ M if parent is not None else M

def vertex_normals(V, F):
    N = np.zeros_like(V, dtype=float)
    fn = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    for k in range(3): np.add.at(N, F[:, k], fn)
    ln = np.linalg.norm(N, axis=1, keepdims=True); ln[ln == 0] = 1
    return N / ln

class Scene:
    def __init__(self): self.tris, self.norms, self.cols = [], [], []
    def add(self, node, mesh, color, alpha=1.0, metal=False):
        V, F = mesh
        W = (node.M[:3, :3] @ V.T).T + node.M[:3, 3]
        smooth = len(V) > 8 and mesh is not CUBE
        if smooth:
            Ninv = np.linalg.inv(node.M[:3, :3]).T
            VN = (Ninv @ vertex_normals(V, F).T).T
            VN /= np.linalg.norm(VN, axis=1, keepdims=True)
        rgba = to_rgba(color)
        for f in F:
            t = W[f]
            if smooth: n = VN[f]
            else:
                fn = np.cross(t[1] - t[0], t[2] - t[0]); l = np.linalg.norm(fn)
                if l == 0: continue
                n = np.repeat((fn / l)[None], 3, 0)
            self.tris.append(t); self.norms.append(n); self.cols.append((rgba, alpha, metal))

# ---------------- primitive meshes (local Unity coords) ----------------
def cube():
    V = np.array([[x, y, z] for x in (-.5, .5) for y in (-.5, .5) for z in (-.5, .5)])
    F = [[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]]
    return V, np.array(F)

def sphere(nu=28, nv=18):
    V, F = [], []
    for i in range(nv + 1):
        t = math.pi * i / nv
        for j in range(nu):
            p = 2 * math.pi * j / nu
            V.append([0.5 * math.sin(t) * math.cos(p), 0.5 * math.cos(t), 0.5 * math.sin(t) * math.sin(p)])
    for i in range(nv):
        for j in range(nu):
            a, b = i * nu + j, i * nu + (j + 1) % nu
            c, d = a + nu, b + nu
            F += [[a, b, c], [b, d, c]]
    return np.array(V), np.array(F)

def lathe(profile, seg=32):
    V, F = [], []
    for r, y in profile:
        for j in range(seg):
            a = 2 * math.pi * j / seg
            V.append([r * math.cos(a), y, r * math.sin(a)])
    for i in range(len(profile) - 1):
        for j in range(seg):
            a, b = i * seg + j, i * seg + (j + 1) % seg
            F += [[a, b, a + seg], [b, b + seg, a + seg]]
    return np.array(V), np.array(F)

def cylinder(seg=24):  # Unity cylinder: radius .5, y -1..1
    return lathe([(0, -1), (0.5, -1), (0.5, 1), (0, 1)], seg)

def tube(loop, radius, sides=8):
    loop = np.array(loop); n = len(loop); V, F = [], []
    for i in range(n):
        p = loop[i]; t = loop[(i + 1) % n] - loop[i - 1]; t /= np.linalg.norm(t)
        n1 = p - np.dot(p, t) * t; n1 /= np.linalg.norm(n1); b = np.cross(t, n1)
        for s in range(sides):
            a = 2 * math.pi * s / sides
            V.append(p + (n1 * math.cos(a) + b * math.sin(a)) * radius)
    for i in range(n):
        for s in range(sides):
            a = i * sides + s; b = i * sides + (s + 1) % sides
            c = ((i + 1) % n) * sides + s; d = ((i + 1) % n) * sides + (s + 1) % sides
            F += [[a, b, c], [b, d, c]]
    return np.array(V), np.array(F)

def ring(major, tub):
    return tube([[math.cos(a) * major, 0, math.sin(a) * major] for a in np.linspace(0, 2 * math.pi, 48, endpoint=False)], tub)

def tennis_seam():
    a, b = 0.68, 0.32
    pts = [[a * math.cos(t) + b * math.cos(3 * t), a * math.sin(t) - b * math.sin(3 * t), 2 * math.sqrt(a * b) * math.sin(2 * t)]
           for t in np.linspace(0, 2 * math.pi, 96, endpoint=False)]
    return tube(np.array(pts) * 0.503, 0.022, 6)

def prism(n):
    prof = []
    V, F = [], []
    for i in range(n):
        a0, a1 = 2 * math.pi * i / n, 2 * math.pi * (i + 1) / n
        c0 = [math.cos(a0) * .5, math.sin(a0) * .5]; c1 = [math.cos(a1) * .5, math.sin(a1) * .5]
        s = len(V)
        V += [[c0[0], -.5, c0[1]], [c1[0], -.5, c1[1]], [c0[0], .5, c0[1]], [c1[0], .5, c1[1]], [0, .5, 0], [0, -.5, 0]]
        F += [[s, s + 1, s + 2], [s + 1, s + 3, s + 2], [s + 4, s + 2, s + 3], [s + 5, s, s + 1]]
    return np.array(V), np.array(F)

def patch(sides, radius, spin):
    rings, per, lift = 8, 8, .508; m = sides * per
    V = [[0, lift, 0]]
    for r in range(1, rings + 1):
        for j in range(m):
            e, f = j // per, (j % per) / per
            a0 = math.radians(e * 360 / sides + spin); a1 = math.radians((e + 1) * 360 / sides + spin)
            fl = (np.array([math.cos(a0), math.sin(a0)]) * (1 - f) + np.array([math.cos(a1), math.sin(a1)]) * f) * radius * r / rings
            p = np.array([fl[0], .5, fl[1]]); V.append(p / np.linalg.norm(p) * lift)
    F = [[0, 1 + (j + 1) % m, 1 + j] for j in range(m)]
    for r in range(1, rings):
        i0, o0 = 1 + (r - 1) * m, 1 + r * m
        for j in range(m):
            F += [[i0 + j, o0 + (j + 1) % m, o0 + j], [i0 + j, i0 + (j + 1) % m, o0 + (j + 1) % m]]
    return np.array(V), np.array(F)

CUBE, SPHERE, CYL = cube(), sphere(), cylinder()
MESH_CACHE = {}
def cached(key, fn):
    if key not in MESH_CACHE: MESH_CACHE[key] = fn()
    return MESH_CACHE[key]

def P(r, y): return (r, y)

# ---------------- builder helpers mirroring C# ----------------
S = None
def col(h, fb="#9AA3AD"): return h if h else fb
def shade(h, f):
    r, g, b, a = to_rgba(h); return (r * f, g * f, b * f)
def box(parent, pos, size, color, euler_=None, alpha=1, metal=False):
    n = Node(parent, pos, euler(*euler_) if euler_ else None, size); S.add(n, CUBE, color, alpha, metal); return n
def cyl(parent, pos, dia, ln, color, euler_=None, metal=False):
    n = Node(parent, pos, euler(*euler_) if euler_ else None, (dia, ln / 2, dia)); S.add(n, CYL, color, 1, metal); return n
def prim_sphere(parent, pos, scale, color):
    n = Node(parent, pos, None, scale); S.add(n, SPHERE, color); return n
def part(parent, mesh, pos, scale, color, euler_=None, rot=None, alpha=1, metal=False):
    R = rot if rot is not None else (euler(*euler_) if euler_ else None)
    n = Node(parent, pos, R, scale); S.add(n, mesh, color, alpha, metal); return n

# ---------------- items ----------------
def ball(root, d, k):
    size = d.get("size", [0.22])
    sz = np.array(size if len(size) >= 3 else [size[0]] * 3) * k
    style = d.get("style", "")
    main = d.get("color", "#E06A1B")
    accent = d.get("color2", "#F5F5F5" if style == "tennis" else "#151515")
    b = prim_sphere(root, (0, sz[1] / 2, 0), sz, main)
    if style == "basketball":
        g = cached("rg", lambda: ring(0.503, 0.012))
        part(b, g, (0, 0, 0), 1, accent); part(b, g, (0, 0, 0), 1, accent, (0, 0, 90))
        sd = cached("rs", lambda: ring(0.503 * math.sin(math.radians(50)), 0.012)); off = 0.503 * math.cos(math.radians(50))
        part(b, sd, (off, 0, 0), 1, accent, (0, 0, 90)); part(b, sd, (-off, 0, 0), 1, accent, (0, 0, 90))
    elif style == "tennis":
        part(b, cached("ts", tennis_seam), (0, 0, 0), 1, accent)
    elif style == "soccer":
        pent = cached("p5", lambda: patch(5, 0.182, 0)); g = 1.618
        for dvec in [(0, 1, g), (0, 1, -g), (0, -1, g), (0, -1, -g), (1, g, 0), (1, -g, 0), (-1, g, 0), (-1, -g, 0), (g, 0, 1), (g, 0, -1), (-g, 0, 1), (-g, 0, -1)]:
            part(b, pent, (0, 0, 0), 1, accent, rot=from_to((0, 1, 0), dvec))
    elif style == "panels":
        face = cached("p4", lambda: patch(4, 0.7071, 45))
        for dvec, c in [((1, 0, 0), accent), ((-1, 0, 0), accent), ((0, 1, 0), "#F5F5F5"), ((0, -1, 0), "#F5F5F5"), ((0, 0, 1), main), ((0, 0, -1), main)]:
            part(b, face, (0, 0, 0), 1, c, rot=from_to((0, 1, 0), dvec))
    elif style == "rugby":
        sm = cached("rr", lambda: ring(0.503, 0.008))
        for a in (0, 90): part(b, sm, (0, 0, 0), 1, accent, (0, 0, a))
        for i in range(6): box(b, (0, 0.5, -0.15 + i * 0.06), (0.14, 0.025, 0.022), "#F5F5F5")
        box(b, (0, 0.505, 0), (0.02, 0.02, 0.34), "#F5F5F5")

def dumbbell(root, d, k):
    r = 0.065 * k; inner = r * 0.866; ln = 0.075 * k; gap = 0.1 * k
    main = d.get("color", "#444444"); up = (90, 0, 0)
    for s in (-1, 1):
        part(root, cached("pr6", lambda: prism(6)), (0, inner, s * (gap + ln / 2)), (2 * r, ln, 2 * r), main, up)
        cyl(root, (0, inner, s * (gap - 0.006 * k)), 0.046 * k, 0.012 * k, "#D0D0D0", up, metal=True)
    cyl(root, (0, inner, 0), 0.03 * k, 2 * gap, "#D0D0D0", up, metal=True)
    cyl(root, (0, inner, 0), 0.033 * k, 1.2 * gap, "#8A8A8A", up, metal=True)

def bottle(root, d, k):
    body = cached("bottle", lambda: lathe([P(0, 0), P(0.032, 0), P(0.036, 0.004), P(0.037, 0.012), P(0.037, 0.15), P(0.035, 0.172), P(0.026, 0.192), P(0.018, 0.2), P(0.018, 0.21), P(0, 0.21)]))
    part(root, body, (0, 0, 0), k, d.get("color", "#2E86DE"))
    part(root, cached("blabel", lambda: lathe([P(0.0378, 0.05), P(0.0378, 0.115)])), (0, 0, 0), k, d.get("color2", "#F2F2F2"))
    part(root, cached("bcap", lambda: lathe([P(0, 0), P(0.021, 0), P(0.021, 0.022), P(0.012, 0.03), P(0.007, 0.042), P(0, 0.042)])), (0, 0.205 * k, 0), k, "#1E1E1E")

def racket(root, d, k):
    bad = d.get("style") == "badminton"
    w = (0.2 if bad else 0.26) * k; l = (0.24 if bad else 0.32) * k; tub = (0.005 if bad else 0.011) * k
    handleL = (0.2 if bad else 0.27) * k; shaftL = (0.22 if bad else 0.1) * k
    total = l + shaftL + handleL; y = 0.016 * k; headZ = total / 2 - l / 2; neckZ = headZ - l / 2
    main = d.get("color", "#1F3A93"); up = (90, 0, 0)
    part(root, ring(0.5, tub / w), (0, y, headZ), (w, w, l), main)
    nx, nz = (6, 8) if bad else (8, 10)
    for i in range(1, nx + 1):
        x = (i / (nx + 1) - .5) * w; half = l / 2 * math.sqrt(1 - (2 * x / w) ** 2)
        box(root, (x, y, headZ), (0.0018 * k, 0.0018 * k, 2 * half), "#F0F0F0")
    for i in range(1, nz + 1):
        z = (i / (nz + 1) - .5) * l; half = w / 2 * math.sqrt(1 - (2 * z / l) ** 2)
        box(root, (0, y, headZ + z), (2 * half, 0.0018 * k, 0.0018 * k), "#F0F0F0")
    if bad:
        cyl(root, (0, y, neckZ - shaftL / 2), 0.008 * k, shaftL, "#C8C8C8", up, metal=True)
    else:
        for s in (-1, 1):
            a = np.array([s * w * 0.28, y, neckZ + 0.02 * k]); b = np.array([0, y, neckZ - shaftL])
            dvec = a - b; L = np.linalg.norm(dvec)
            n = Node(root, (a + b) / 2, from_to((0, 0, 1), dvec), (0.012 * k, 0.014 * k, L)); S.add(n, CUBE, main)
    hz = neckZ - shaftL - handleL / 2
    part(root, cached("pr8", lambda: prism(8)), (0, y, hz), (0.03 * k, handleL, 0.03 * k), d.get("color2", "#1E1E1E"), up)
    cyl(root, (0, y, hz - handleL / 2), 0.034 * k, 0.008 * k, main, up)

def bat(root, d, k):
    up = (90, 0, 0); main = d.get("color", "#E8D3A2")
    if d.get("style") == "baseball":
        m = cached("bb", lambda: lathe([P(0, 0), P(0.028, 0), P(0.03, 0.012), P(0.015, 0.03), P(0.013, 0.25), P(0.02, 0.45), P(0.032, 0.64), P(0.034, 0.79), P(0.03, 0.83), P(0, 0.84)]))
        pos = (0, 0.034 * k, -0.42 * k)
        part(root, m, pos, k, main, up)
        part(root, cached("tape", lambda: lathe([P(0.0153, 0.032), P(0.0143, 0.2)])), pos, k, d.get("color2", "#1E1E1E"), up)
    else:
        box(root, (0, 0.017 * k, 0.13 * k), np.array([0.108, 0.034, 0.56]) * k, main)
        box(root, (0, 0.04 * k, 0.1 * k), np.array([0.05, 0.03, 0.4]) * k, main)
        box(root, (0, 0.0345 * k, -0.1 * k), np.array([0.1, 0.002, 0.06]) * k, d.get("color2", "#C0392B"))
        part(root, cached("ch", lambda: lathe([P(0, 0), P(0.017, 0), P(0.017, 0.29), P(0.012, 0.3), P(0, 0.3)])), (0, 0.017 * k, -0.45 * k), k, "#1E1E1E", up)

def cone(root, d, k):
    main = d.get("color", "#FF7F11")
    box(root, (0, 0.0125 * k, 0), np.array([0.26, 0.025, 0.26]) * k, shade(main, 0.85))
    part(root, cached("cone", lambda: lathe([P(0, 0.025), P(0.115, 0.025), P(0.11, 0.035), P(0.03, 0.3), P(0.02, 0.31), P(0, 0.31)])), (0, 0, 0), k, main)
    part(root, cached("cband", lambda: lathe([P(0.0763, 0.15), P(0.0612, 0.2)])), (0, 0, 0), k, "#F4F4F4")

def trophy(root, d, k):
    gold = d.get("color", "#D4AF37")
    box(root, (0, 0.03 * k, 0), np.array([0.12, 0.06, 0.12]) * k, "#1C1C1C")
    box(root, (0, 0.03 * k, 0.0605 * k), np.array([0.07, 0.025, 0.002]) * k, gold, metal=True)
    cup = cached("cup", lambda: lathe([P(0, 0), P(0.045, 0), P(0.045, 0.01), P(0.03, 0.02), P(0.012, 0.04), P(0.01, 0.1), P(0.018, 0.115), P(0.05, 0.14), P(0.068, 0.2), P(0.072, 0.27), P(0.066, 0.272), P(0.06, 0.21), P(0.04, 0.17), P(0, 0.162)]))
    part(root, cup, (0, 0.06 * k, 0), k, gold, metal=True)
    for s in (-1, 1):
        part(root, cached("th", lambda: ring(0.5, 0.09)), (s * 0.08 * k, 0.26 * k, 0), np.array([0.05, 0.05, 0.09]) * k, gold, (90, 0, 0), metal=True)

def medal(root, d, k):
    gold = d.get("color", "#D4AF37")
    part(root, cached("md", lambda: lathe([P(0, 0), P(0.035, 0), P(0.036, 0.004), P(0.033, 0.006), P(0.025, 0.0055), P(0, 0.007)])), (0, 0, 0), k, gold, metal=True)
    part(root, cached("ml", lambda: ring(0.5, 0.15)), (0, 0.003 * k, -0.04 * k), 0.014 * k, gold, metal=True)
    for s in (-1, 1):
        n = Node(root, (s * 0.025 * k, 0.002 * k, -0.12 * k), euler(0, -s * 12, 0), np.array([0.03, 0.002, 0.16]) * k)
        S.add(n, CUBE, d.get("color2", "#C0392B"))

def key(root, d, k):
    steel = d.get("color", "#B8B8B8"); y = 0.005 * k
    part(root, cached("kb", lambda: ring(0.5, 0.16)), (0, y, -0.03 * k), 0.03 * k, steel, metal=True)
    box(root, (0, y, 0.015 * k), np.array([0.008, 0.004, 0.055]) * k, steel, metal=True)
    for x, z, sx in ((0.007, 0.037, 0.008), (0.006, 0.027, 0.006), (0.008, 0.017, 0.009)):
        box(root, (x * k, y, z * k), np.array([sx, 0.004, 0.005]) * k, steel, metal=True)
    part(root, cached("kr", lambda: ring(0.5, 0.08)), (0, y, -0.056 * k), 0.025 * k, steel, metal=True)
    box(root, (0, 0.003 * k, -0.086 * k), np.array([0.028, 0.005, 0.04]) * k, d.get("color2", "#F1C40F"))

ITEM = dict(ball=ball, dumbbell=dumbbell, bottle=bottle, racket=racket, bat=bat, cone=cone, trophy=trophy, medal=medal, key=key)

def build_item(parent, d):
    ITEM[d["kind"]](parent, d, d.get("scale", 1.0))

# ---------------- furniture (only what the close-up previews need) ----------------
LEVELS = {1: [""], 2: ["bottom", "top"], 3: ["bottom", "middle", "top"], 4: ["bottom", "lower", "upper", "top"]}
class Surf:
    def __init__(self, node, local, area, spacing):
        self.node = Node(node, local); self.area = area; self.sp = spacing; self.used = 0
        self.cols = max(1, math.floor(area[0] / spacing + 1e-3)); self.rows = max(1, math.floor(area[1] / spacing + 1e-3))
        self.taken = []
    def local(self, i):
        c, r = i % self.cols, (i // self.cols) % self.rows
        return np.array([(c + .5) * self.area[0] / self.cols - self.area[0] / 2, 0, (r + .5) * self.area[1] / self.rows - self.area[1] / 2])
    def next(self):
        best, score = -1, -1e9
        for i in range(self.cols * self.rows):
            if i in self.taken: continue
            p = self.local(i)
            sc = -p @ p if not self.taken else min(((self.local(t) - p) ** 2).sum() for t in self.taken)
            if sc > score + 1e-4: best, score = i, sc
        self.taken.append(best); self.used += 1
        return Node(self.node, self.local(best))

def shelves(root, fid, s, levels, color, surfs):
    th = 0.04
    box(root, (-s[0] / 2 + th / 2, s[1] / 2, 0), (th, s[1], s[2]), color)
    box(root, (s[0] / 2 - th / 2, s[1] / 2, 0), (th, s[1], s[2]), color)
    box(root, (0, s[1] / 2, -s[2] / 2 + th / 2), (s[0], s[1], th), color)
    box(root, (0, s[1] - th / 2, 0), (s[0], th, s[2]), color)
    gap = (s[1] - 0.1) / levels
    for kk in range(levels):
        y = 0.08 + kk * gap
        box(root, (0, y - th / 2, 0), (s[0] - 2 * th, th, s[2] - th), shade(color, 0.8))
        name = LEVELS[levels][kk]
        surfs[f"{fid}_{name}" if name else fid] = Surf(root, (0, y, 0), (s[0] - .2, s[2] - .15), .4)
    if levels > 1: surfs[fid] = [f"{fid}_{n}" for n in LEVELS[levels]]

def furniture(parent, f, surfs, open_doors=False):
    kind = f["kind"]; s = f.get("size")
    defaults = {"rack": (3, 2, .6), "cabinet": (1.6, 1.9, .5), "locker": (.6, 1.9, .55), "ball_cart": (1, .9, .6), "bench": (2, .45, .45), "mat": (1.8, .02, .8)}
    s = s or defaults[kind]
    color = f.get("color", {"locker": "#3B6EA5", "ball_cart": "#4A4A4A", "bench": "#A0784C"}.get(kind, "#8A6F4E"))
    root = Node(parent, (0, 0, 0))
    if kind == "rack":
        shelves(root, f["id"], s, f.get("levels", 3), color, surfs)
    elif kind == "cabinet":
        shelves(root, f["id"], s, f.get("levels", 2), color, surfs)
        hinge = Node(root, (-s[0] / 2, 0, s[2] / 2), euler(0, -100 if open_doors else 0, 0))
        box(hinge, (s[0] / 2, s[1] / 2, 0.015), (s[0] - .01, s[1] - .02, .03), "#BFE6FF", alpha=0.25)
    elif kind == "locker":
        th = .03
        box(root, (-s[0] / 2 + th / 2, s[1] / 2, 0), (th, s[1], s[2]), color); box(root, (s[0] / 2 - th / 2, s[1] / 2, 0), (th, s[1], s[2]), color)
        box(root, (0, s[1] / 2, -s[2] / 2 + th / 2), (s[0], s[1], th), color); box(root, (0, s[1] - th / 2, 0), (s[0], th, s[2]), color)
        box(root, (0, .03, 0), (s[0], .06, s[2]), color)
        box(root, (0, s[1] - .35, -.04), (s[0] - 2 * th, .02, s[2] - .1), color)
        box(root, (0, s[1] - .5, -s[2] / 2 + th + .03), (.02, .02, .06), "#BBBBBB", metal=True)
        hinge = Node(root, (-s[0] / 2, 0, s[2] / 2), euler(0, -100 if open_doors else 0, 0))
        box(hinge, (s[0] / 2, s[1] / 2, .015), (s[0] - .01, s[1] - .02, .03), shade(color, .88))
        box(hinge, (s[0] - .08, s[1] * .5, .045), (.03, .12, .03), "#BBBBBB", metal=True)
        for v in range(3): box(hinge, (s[0] / 2, s[1] - .25 - v * .06, .032), (s[0] * .6, .02, .005), "#222222")
        surfs[f["id"]] = Surf(root, (0, .06, 0), (s[0] - .12, s[2] - .12), .22)
    elif kind == "ball_cart":
        bottom, bar, pitch = .35, .012, .08; h = s[1] - bottom
        box(root, (0, bottom, 0), (s[0], .03, s[2]), color)
        for ry in (bottom + h * .5, s[1]):
            box(root, (0, ry, s[2] / 2), (s[0], .02, .02), "#BBBBBB", metal=True); box(root, (0, ry, -s[2] / 2), (s[0], .02, .02), "#BBBBBB", metal=True)
            box(root, (-s[0] / 2, ry, 0), (.02, .02, s[2]), "#BBBBBB", metal=True); box(root, (s[0] / 2, ry, 0), (.02, .02, s[2]), "#BBBBBB", metal=True)
        nx, nz = max(2, round(s[0] / pitch)), max(2, round(s[2] / pitch))
        for i in range(nx + 1):
            for bz in (-s[2] / 2, s[2] / 2): box(root, (-s[0] / 2 + i * s[0] / nx, bottom + h / 2, bz), (bar, h, bar), color)
        for i in range(1, nz):
            for bx in (-s[0] / 2, s[0] / 2): box(root, (bx, bottom + h / 2, -s[2] / 2 + i * s[2] / nz), (bar, h, bar), color)
        for lx, lz in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
            p = np.array([lx * (s[0] / 2 - .06), 0, lz * (s[2] / 2 - .06)])
            box(root, p + [0, bottom / 2, 0], (.04, bottom, .04), "#BBBBBB", metal=True)
            cyl(root, p + [0, .05, 0], .1, .04, "#222222", (0, 0, 90))
        surfs[f["id"]] = Surf(root, (0, bottom + .03, 0), (s[0] - .15, s[2] - .12), .22)
    elif kind == "bench":
        box(root, (0, s[1] - .03, 0), (s[0], .06, s[2]), color)
        for sx in (-1, 1): box(root, (sx * (s[0] / 2 - .15), (s[1] - .06) / 2, 0), (.06, s[1] - .06, s[2] * .8), "#222222")
        surfs[f["id"]] = Surf(root, (0, s[1], 0), (s[0] - .2, s[2] - .1), .45)
    elif kind == "mat":
        box(root, (0, s[1] / 2, 0), s, color)
        surfs[f["id"]] = Surf(root, (0, s[1], 0), (s[0] - .2, s[2] - .2), .45)
    return root

def place(items, surfs):
    for it in items:
        tgt = it.get("on")
        if tgt not in surfs: continue
        sv = surfs[tgt]
        if isinstance(sv, list): sv = next(surfs[n] for n in sv if surfs[n].used < surfs[n].cols * surfs[n].rows)
        build_item(sv.next(), it)

# ---------------- rendering (z-buffer, Unity coords, y up) ----------------
def render_image(scene, W=900, H=600, elev=20, azim=-30, fov=28, bg=(0.957, 0.945, 0.925), ss=2):
    T = np.array(scene.tris); N = np.array(scene.norms)
    pts = T.reshape(-1, 3); center = (pts.min(0) + pts.max(0)) / 2; radius = np.linalg.norm(pts.max(0) - pts.min(0)) / 2
    el, az = math.radians(elev), math.radians(azim)
    view_dir = np.array([math.cos(el) * math.sin(az), math.sin(el), math.cos(el) * math.cos(az)])
    dist = radius / math.tan(math.radians(fov / 2)) * 1.05
    cam = center + view_dir * dist
    f = -view_dir; r = np.cross([0, 1, 0], f); r /= np.linalg.norm(r); u = np.cross(f, r)
    rel = T - cam
    depth = rel @ f; sx = (rel @ r) / depth; sy = (rel @ u) / depth
    W2, H2 = W * ss, H * ss
    span = max((sx.max() - sx.min()) / W2, (sy.max() - sy.min()) / H2) * 1.08
    cx, cy = (sx.max() + sx.min()) / 2, (sy.max() + sy.min()) / 2
    px = (sx - cx) / span + W2 / 2; py = H2 / 2 - (sy - cy) / span
    img = np.ones((H2, W2, 3)) * np.array(bg); zb = np.full((H2, W2), np.inf)
    key = np.array([0.35, 0.85, 0.55]); key /= np.linalg.norm(key)
    fill = np.array([-0.6, 0.3, -0.4]); fill /= np.linalg.norm(fill)
    order = sorted(range(len(T)), key=lambda i: (scene.cols[i][1] < 1, -depth[i].mean() if scene.cols[i][1] < 1 else 0))
    for i in order:
        (cr, cg, cb, _), alpha, metal = scene.cols[i]
        x, y, z = px[i], py[i], depth[i]
        x0, x1 = max(int(x.min()), 0), min(int(x.max()) + 1, W2 - 1)
        y0, y1 = max(int(y.min()), 0), min(int(y.max()) + 1, H2 - 1)
        if x0 > x1 or y0 > y1: continue
        den = (y[1] - y[2]) * (x[0] - x[2]) + (x[2] - x[1]) * (y[0] - y[2])
        if abs(den) < 1e-12: continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + .5, np.arange(y0, y1 + 1) + .5)
        w0 = ((y[1] - y[2]) * (gx - x[2]) + (x[2] - x[1]) * (gy - y[2])) / den
        w1 = ((y[2] - y[0]) * (gx - x[2]) + (x[0] - x[2]) * (gy - y[2])) / den
        w2 = 1 - w0 - w1
        m = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        if not m.any(): continue
        zz = w0 * z[0] + w1 * z[1] + w2 * z[2]
        sub = zb[y0:y1 + 1, x0:x1 + 1]
        m &= zz < sub - 1e-5
        if not m.any(): continue
        n = w0[m, None] * N[i][0] + w1[m, None] * N[i][1] + w2[m, None] * N[i][2]
        n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-9
        vdir = -f
        n = np.where((n @ vdir)[:, None] < 0, -n, n)  # two-sided
        dk = np.clip(n @ key, 0, 1); df = np.clip(n @ fill, 0, 1)
        h = key + vdir; h /= np.linalg.norm(h)
        spec = np.clip(n @ h, 0, 1)
        base = np.array([cr, cg, cb])
        if metal:
            refl = 0.5 + 0.5 * n[:, 1]
            col = base * (0.25 + 0.55 * dk[:, None] + 0.35 * refl[:, None]) + (spec ** 40)[:, None] * 0.9
        else:
            col = base * (0.38 + 0.62 * dk[:, None] + 0.18 * df[:, None]) + (spec ** 24)[:, None] * 0.12
        col = np.clip(col, 0, 1)
        region = img[y0:y1 + 1, x0:x1 + 1]
        if alpha < 1:
            region[m] = region[m] * (1 - alpha) + col * alpha
        else:
            region[m] = col; sub[m] = zz[m]
    return img.reshape(H, ss, W, ss, 3).mean(axis=(1, 3))


def yaw(faces):
    if isinstance(faces, (int, float)): return float(faces)
    return {"north": 0, "east": 90, "south": 180, "west": 270}.get((faces or "north").lower(), 0)

def court_lines(ax, zn):
    """Half-court markings, same numbers as CourtLines() in SportsRoomDecor.cs (x across, v away from the baseline)."""
    x, z, w, d = zn["rect"]; side = zn.get("hoop", "north")
    o, ex, ev = {"north": ((x + w / 2, z + d), (-1, 0), (0, -1)), "south": ((x + w / 2, z), (1, 0), (0, 1)),
                 "east": ((x + w, z + d / 2), (0, 1), (-1, 0)), "west": ((x, z + d / 2), (0, -1), (1, 0))}[side]
    W, D = (w, d) if side in ("north", "south") else (d, w)
    def draw(pts):
        ax.plot([o[0] + u * ex[0] + v * ev[0] for u, v in pts], [o[1] + u * ex[1] + v * ev[1] for u, v in pts], color="white", lw=1.4, zorder=1.3)
    def arc(cv, r, a0, a1):
        draw([(r * math.cos(math.radians(a)), cv + r * math.sin(math.radians(a))) for a in np.linspace(a0, a1, 60)])
    hw, key_w, key_d, hoop_v = W / 2, min(3.6, W * 0.3), min(5.8, D * 0.62), 0.55
    draw([(-hw, 0), (hw, 0), (hw, D), (-hw, D), (-hw, 0)])
    draw([(-key_w / 2, 0), (-key_w / 2, key_d), (key_w / 2, key_d), (key_w / 2, 0)])
    arc(key_d, key_w / 2, 0, 360); arc(hoop_v, 1.25, 0, 180)
    three = min(6.75, hw - 0.9)
    if hoop_v + three < D - 0.3:
        draw([(three, 0), (three, hoop_v)]); arc(hoop_v, three, 0, 180); draw([(-three, hoop_v), (-three, 0)])
    arc(D, min(1.8, W * 0.14), 180, 360)


def floor_plan(L, path):
    from matplotlib.patches import Rectangle, Polygon, FancyArrow
    global S
    S = Scene(); surfs = {}; world = Node(); fpos = {}
    for f in L["furniture"]:
        x, z = f["at"]; root = Node(world, (x, 0, z), euler(0, yaw(f.get("faces")), 0))
        fpos[f["id"]] = (x, z, yaw(f.get("faces")))
        if f["kind"] in ("rack", "cabinet", "locker", "ball_cart", "bench", "mat"):
            furniture(root, f, surfs)
    fig, ax = plt.subplots(figsize=(17, 9.6), facecolor="white")
    for r in L["rooms"]:
        x, z, w, d = r["rect"]
        ax.add_patch(Rectangle((x, z), w, d, facecolor=r.get("floor_color", "#C8A878"), edgecolor="#333", lw=4, zorder=1))
        if r.get("ceiling"):
            ax.add_patch(Rectangle((x, z), w, d, facecolor="none", edgecolor="#555", hatch="//", lw=0, alpha=0.25, zorder=1.1))
        ax.text(x + w / 2, z + d - 0.6, r.get("label", r["id"]) + ("  (roofed: hidden from overhead camera)" if r.get("ceiling") else ""),
                ha="center", va="top", fontsize=10, weight="bold", color="#333", zorder=5)
    for zn in L.get("zones", []):
        x, z, w, d = zn["rect"]
        ax.add_patch(Rectangle((x, z), w, d, facecolor=zn.get("color", "#ddd"), edgecolor="none", alpha=0.55, zorder=1.2))
        ax.text(x + 0.25, z + 0.25, zn["label"], fontsize=8, color="#444", style="italic", zorder=5)
        if zn.get("style") == "basketball": court_lines(ax, zn)
    for dr in L.get("doors", []):
        x, z = dr["at"]; w = dr.get("width", 1.2)
        ax.plot([x, x], [z - w / 2, z + w / 2], color="#C0392B" if not dr.get("open") else "#27AE60", lw=7, solid_capstyle="butt", zorder=6)
        ax.text(x + 0.2, z, dr["id"] + (" (closed)" if not dr.get("open") else " (open)"), fontsize=7, va="center", zorder=7)
    defaults = {"rack": (3, 2, .6), "cabinet": (1.6, 1.9, .5), "locker": (.6, 1.9, .55), "bench": (2, .45, .45), "table": (1.6, .8, .8),
                "mat": (1.8, .02, .8), "hoop": (1.8, 3.05, 1.2), "goal": (3, 2.2, 1.5), "ball_cart": (1, .9, .6), "treadmill": (.9, 1.3, 2),
                "exercise_bike": (.6, 1.2, 1.2), "rowing_machine": (.6, .5, 2.2), "bench_press": (1.6, 1.4, 1.8), "squat_rack": (1.6, 2.3, 1.3),
                "hurdle": (1.2, .8, .3), "box": (1, 1, 1)}
    fcol = {"rack": "#8A6F4E", "cabinet": "#5B4636", "locker": "#3B6EA5", "bench": "#A0784C", "mat": "#2E86DE", "hoop": "#E8601C", "goal": "#DDDDDD",
            "ball_cart": "#4A4A4A", "treadmill": "#3C3C3C", "exercise_bike": "#C0392B", "rowing_machine": "#2C3E50", "bench_press": "#555555",
            "squat_rack": "#2C2C2C", "hurdle": "#F1C40F"}
    for f in L["furniture"]:
        x, z, a = fpos[f["id"]]; sx, _, sz = f.get("size") or defaults[f["kind"]]
        R = euler(0, a, 0)
        corners = [R @ np.array([cx * sx / 2, 0, cz * sz / 2]) for cx, cz in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        ax.add_patch(Polygon([(x + c[0], z + c[2]) for c in corners], closed=True, facecolor=f.get("color", fcol.get(f["kind"], "#999")),
                             edgecolor="black", lw=0.8, alpha=0.9, zorder=3))
        front = R @ np.array([0, 0, sz / 2])
        ax.plot([x + corners[2][0], x + corners[3][0]], [z + corners[2][2], z + corners[3][2]], color="white", lw=1.6, zorder=3.5)
        extra = (" LOCKED" if f.get("locked") else "")
        ax.text(x, z, f["id"] + extra, fontsize=6.5, ha="center", va="center", zorder=4, color="white" if f["kind"] in ("locker", "ball_cart", "treadmill", "squat_rack", "rowing_machine", "bench_press", "cabinet") else "black",
                rotation=90 if f["kind"] == "locker" else 0, weight="bold" if f.get("locked") else "normal")
    marker = {"ball": "o", "dumbbell": "H", "bottle": "s", "racket": "P", "bat": "d", "cone": "^", "trophy": "*", "medal": "D", "key": "X"}
    for it in L["items"]:
        if "at" in it:
            p = np.array([it["at"][0], 0, it["at"][1]])
        else:
            tgt = it.get("on")
            if tgt not in surfs:
                base = tgt.rsplit("_", 1)[0] if tgt and tgt not in fpos else tgt
                if base not in fpos: continue
                p = np.array([fpos[base][0], 0, fpos[base][1]])
                ax.scatter([p[0]], [p[2]], s=95, marker=marker.get(it["kind"], "o"), c=[it.get("color", "#E06A1B")], edgecolors="black", zorder=8)
                continue
            sv = surfs[tgt]
            if isinstance(sv, list): sv = next(surfs[n] for n in sv if surfs[n].used < surfs[n].cols * surfs[n].rows)
            p = sv.next().M[:3, 3]
        ax.scatter([p[0]], [p[2]], s=95 if it["kind"] != "key" else 120, marker=marker.get(it["kind"], "o"), c=[it.get("color", "#E06A1B")],
                   edgecolors="black", linewidths=0.9, zorder=8)
    rb = L["robot"]; rx, rz = rb["at"]; ra = math.radians(yaw(rb.get("faces")))
    ax.add_patch(FancyArrow(rx, rz, 0.9 * math.sin(ra), 0.9 * math.cos(ra), width=0.25, color="#8E44AD", zorder=9))
    ax.text(rx + 0.4, rz - 0.4, "robot start", color="#8E44AD", fontsize=9, weight="bold", zorder=9)
    handles = [plt.Line2D([], [], marker=m, ls="", color="#777", markeredgecolor="black", markersize=9, label=k) for k, m in marker.items()]
    ax.legend(handles=handles, title="item symbols (colour = real item colour)", loc="upper center", bbox_to_anchor=(0.5, -0.04), ncol=9, frameon=False, fontsize=9)
    ax.set_aspect("equal"); ax.set_xlim(-0.8, 38.8); ax.set_ylim(-0.8, 20.8)
    ax.set_xticks(range(0, 39, 2)); ax.set_yticks(range(0, 21, 2)); ax.grid(alpha=0.15)
    ax.set_xlabel("x (metres, east)"); ax.set_ylabel("z (metres, north)")
    ax.set_title(f"{L.get('name', 'layout')}: floor plan (white edge = front of furniture). See the other previews for the 3D models.", fontsize=12, weight="bold")
    plt.tight_layout(); plt.savefig(path, dpi=100); plt.close(fig)

def main():
    global S
    L = json.load(open(LAYOUT))
    items = L["items"]
    # ---- 1. item catalogue ----
    seen, uniq = set(), []
    for it in items:
        key = (it["kind"], it.get("style"), it.get("color"), it.get("scale"), tuple(it.get("size", [])))
        if key not in seen: seen.add(key); uniq.append(it)
    cols_n = 6; rows_n = math.ceil(len(uniq) / cols_n)
    fig = plt.figure(figsize=(cols_n * 3.1, rows_n * 3.2), facecolor="#F4F1EC")
    for i, it in enumerate(uniq):
        S = Scene(); build_item(Node(), it)
        ax = fig.add_subplot(rows_n, cols_n, i + 1); ax.set_axis_off()
        elev, azim = (18, -35) if it["kind"] not in ("racket", "bat", "key", "medal") else (45, -40)
        ax.imshow(render_image(S, 420, 340, elev, azim))
        size = {"ball": f'{max(it.get("size", [0.22])) * 100:.0f} cm ball'}.get(it["kind"], f'scale {it.get("scale", 1)}')
        ids = [x["id"] for x in items if (x["kind"], x.get("style"), x.get("color"), x.get("scale"), tuple(x.get("size", []))) == (it["kind"], it.get("style"), it.get("color"), it.get("scale"), tuple(it.get("size", [])))]
        ax.set_title(", ".join(ids) + f"\n({size})", fontsize=9)
    fig.suptitle("Pickable items exactly as the Scene Builder models them (not to scale with each other)", fontsize=14, weight="bold")
    plt.tight_layout(); plt.savefig(f"{OUT}/items_catalogue.png", dpi=95, facecolor=fig.get_facecolor()); plt.close(fig)

    F = {f["id"]: f for f in L["furniture"]}
    def shot(fname, title, layout, elev, azim, fov=24, size=(1400, 700), floor=None):
        global S
        S = Scene(); surfs = {}; world = Node()
        layout = [e for e in layout if e[0] in F]
        if not layout: return
        for fid, pos, open_doors in layout:
            furniture(Node(world, pos), F[fid], surfs, open_doors)
        if floor: box(world, floor[0], floor[1], floor[2])
        place(items, surfs)
        fig = plt.figure(figsize=(size[0] / 100, size[1] / 100 + 0.4), facecolor="#F4F1EC"); ax = fig.add_axes([0, 0, 1, 0.93]); ax.set_axis_off()
        ax.imshow(render_image(S, size[0], size[1], elev, azim, fov=fov))
        fig.suptitle(title, fontsize=13, weight="bold", y=0.985)
        plt.savefig(f"{OUT}/{fname}", dpi=100, facecolor=fig.get_facecolor()); plt.close(fig)
    # Furniture fronts face +z; x is negated so the left-to-right order reads like the room.
    shot("ball_storage.png", "Ball rack (3 shelves) and ball cart with the three tennis balls",
         [("ball_rack", (0, 0, 0), False), ("ball_cart", (-0.6, 0, 1.0), False)], 16, 0, fov=22,
         floor=((0, -0.05, 0.6), (3.6, 0.1, 2.0), "#D9B27C"))
    shot("racquet_trophy_storage.png", "Racquet & bat rack, trophy shelf, trophy cabinet (glass door shown open)",
         [("bat_rack", (0, 0, 0), False), ("trophy_shelf", (-2.5, 0, 0), False), ("trophy_cabinet", (-4.8, 0, 0), True)], 14, 0, fov=22,
         floor=((-2.3, -0.05, 0.4), (7.6, 0.1, 1.6), "#D9B27C"))
    lockers = [f for f in L["furniture"] if f["kind"] == "locker"][:8]
    shot("lockers.png", "Lockers 1-6, doors shown open: locker_key in locker 2, gold_medal in locked locker 5, green bottle in locker 1",
         [(f["id"], (-(i * 0.62 + (0.4 if i >= 3 else 0)), 0, 0), True) for i, f in enumerate(lockers)], 32, 0, fov=22,
         floor=((-1.75, -0.05, 0.5), (4.6, 0.1, 1.8), "#BFC7D0"))
    shot("gym_corner.png", "Dumbbell rack, blue mat with blue dumbbell, bench with water bottles",
         [("dumbbell_rack", (0, 0, 0), False), ("blue_mat", (0, 0, 1.2), False), ("bench", (-2.4, 0, 1.0), False)], 26, 0, fov=22,
         floor=((-1.2, -0.05, 0.7), (5.2, 0.1, 2.2), "#BFC7D0"))
    floor_plan(L, f"{OUT}/floor_plan.png")
    print("rendered")

main()
