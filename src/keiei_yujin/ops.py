"""生成操作（道具を作る）と、道具による分割。ドラフト。

Abaqus/CAE のパーティション方法（docs/abaqus-partition-coverage.md）を、
「道具を作る生成操作」＋「道具で分割する操作」の組み合わせに分解して実装する。

生成操作は道具の面・稜線・頂点に操作の子としてIDを付ける（D1.F1, K1.E2, ...）。
道具は形状本体 (Model.shape) には含めず Model.tools に持つ。

操作ログの形（JSON の仮形式）:
  {"id": "D1", "type": "datum_plane", "point": P, "normal": N}
  {"id": "D1", "type": "datum_plane", "points": [P, P, P]}
  {"id": "D1", "type": "datum_plane", "edge": "E3", "point": P}         # 辺を含み点を通る
  {"id": "D1", "type": "datum_plane", "offset_face": "F2", "distance": d}
  {"id": "X1", "type": "extend_face", "face": "F7"}                     # 平面・円筒・円錐・球の延長
  {"id": "K1", "type": "sketch", "elements": [...], "closed": true}     # 文字スケッチ（3D 座標 or ID 参照）
  {"id": "T1", "type": "extrude", "profile": "K1.F1", "direction": N, "length": L, "symmetric": true}
  {"id": "W1", "type": "sweep", "profile": ["E5"], "path": ["E9"]}
  {"id": "N1", "type": "patch", "edges": ["E1", "E2", "E3", "E4"]}      # n 辺パッチ
  {"id": "J1", "type": "project", "edges": ["K1.E1"], "face": "F3", "direction": N}
  {"id": "P1", "type": "partition", "tool": "D1.F1"}                    # または ["D1.F1", "X1.F1"]
  {"id": "Q1", "type": "split_edge", "edge": "E3", "t": 0.5}            # 辺の分割（頂点を道具にする）
"""
from __future__ import annotations

import math

from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakeVertex,
    BRepBuilderAPI_MakeWire,
)
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeFilling, BRepOffsetAPI_MakePipe
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.BRepProj import BRepProj_Projection
from OCP.GC import GC_MakeArcOfCircle, GC_MakeCircle, GC_MakeSegment
from OCP.GeomAbs import GeomAbs_C0, GeomAbs_Cylinder, GeomAbs_Plane
from OCP.GeomAPI import GeomAPI_PointsToBSpline
from OCP.Precision import Precision
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_WIRE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Shape
from OCP.collections import Array1_gp_Pnt
from OCP.gp import gp_Ax3, gp_Dir, gp_Pln, gp_Pnt, gp_Vec

from . import refs
from .geometry import centroid, explore
from .lineage import Model

BIG = 1e4


# ---- 道具の生成 ------------------------------------------------------------

def datum_plane(m: Model, op: dict) -> TopoDS_Shape:
    if "points" in op:
        p1, p2, p3 = (refs.point(m, p) for p in op["points"])
        n = gp_Vec(p1, p2).Crossed(gp_Vec(p1, p3))
        pln = gp_Pln(p1, gp_Dir(n))
    elif "edge" in op:
        p = refs.point(m, op["point"])
        a = refs.point(m, {"on": op["edge"], "t": 0.0})
        t = refs.direction(m, {"tangent": op["edge"]})
        n = gp_Vec(t).Crossed(gp_Vec(a, p))
        pln = gp_Pln(a, gp_Dir(n))
    elif "offset_face" in op:
        face = TopoDS.Face(m.ids[op["offset_face"]])
        surf = BRepAdaptor_Surface(face)
        if surf.GetType() != GeomAbs_Plane:
            raise ValueError("offset_face: planar faces only")
        n = refs.direction(m, {"normal": op["offset_face"]})
        c = gp_Pnt(*centroid(face)).Translated(gp_Vec(n).Scaled(float(op["distance"])))
        pln = gp_Pln(c, n)
    else:
        pln = gp_Pln(refs.point(m, op["point"]), refs.direction(m, op["normal"]))
    return BRepBuilderAPI_MakeFace(pln, -BIG, BIG, -BIG, BIG).Face()


def extend_face(m: Model, op: dict) -> TopoDS_Shape:
    """既存面の下地曲面から大きな面を作る（平面・円筒・円錐・球など解析曲面向け）。"""
    face = TopoDS.Face(m.ids[op["face"]])
    surf = BRep_Tool.Surface_s(face)
    u1, u2, v1, v2 = surf.Bounds()
    inf = Precision.Infinite_s()

    def clamp(a, b, periodic):
        if periodic:
            return a, b
        return (max(a, -BIG) if a <= -inf / 2 else a), (min(b, BIG) if b >= inf / 2 else b)

    u1, u2 = clamp(u1, u2, surf.IsUPeriodic())
    v1, v2 = clamp(v1, v2, surf.IsVPeriodic())
    return BRepBuilderAPI_MakeFace(surf, u1, u2, v1, v2, 1e-7).Face()


def _spline(pts: list[gp_Pnt]):
    arr = Array1_gp_Pnt(1, len(pts))
    for i, p in enumerate(pts, 1):
        arr.SetValue(i, p)
    return GeomAPI_PointsToBSpline(arr).Curve()


def sketch(m: Model, op: dict) -> TopoDS_Shape:
    """文字スケッチ。要素は 3D 座標か ID 参照で書く。閉じていて平面なら面、そうでなければ稜線の集まり。

    elements:
      {"line": [P, P]}
      {"polyline": [P, P, ...]}
      {"arc": [P, P, P]}                       3点を通る円弧
      {"circle": {"center": P, "radius": r, "normal": N}}
      {"spline": [P, P, ...]}
    """
    edges = []
    for el in op["elements"]:
        if "line" in el:
            a, b = (refs.point(m, p) for p in el["line"])
            edges.append(BRepBuilderAPI_MakeEdge(GC_MakeSegment(a, b).Value()).Edge())
        elif "polyline" in el:
            pts = [refs.point(m, p) for p in el["polyline"]]
            for a, b in zip(pts, pts[1:]):
                edges.append(BRepBuilderAPI_MakeEdge(GC_MakeSegment(a, b).Value()).Edge())
        elif "arc" in el:
            a, b, c = (refs.point(m, p) for p in el["arc"])
            edges.append(BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(a, b, c).Value()).Edge())
        elif "circle" in el:
            c = el["circle"]
            circ = GC_MakeCircle(refs.point(m, c["center"]), refs.direction(m, c["normal"]), float(c["radius"])).Value()
            edges.append(BRepBuilderAPI_MakeEdge(circ).Edge())
        elif "spline" in el:
            edges.append(BRepBuilderAPI_MakeEdge(_spline([refs.point(m, p) for p in el["spline"]])).Edge())
        else:
            raise ValueError(f"unknown sketch element: {el}")

    if not op.get("closed", False):
        return _compound(edges)
    mw = BRepBuilderAPI_MakeWire()
    for e in edges:
        mw.Add(e)
    wire = mw.Wire()
    if not wire.Closed():
        raise ValueError(f"{op['id']}: sketch is not closed")
    mf = BRepBuilderAPI_MakeFace(wire, True)  # 平面なら面にする
    if mf.IsDone():
        return mf.Face()
    return _fill(list(_sub(wire, TopAbs_EDGE)))  # 非平面ループはパッチ


def extrude(m: Model, op: dict) -> TopoDS_Shape:
    """輪郭（面 → ソリッド、稜線 → 板）を方向に押し出す。symmetric なら両側。"""
    profile = _profile(m, op["profile"])
    d = refs.direction(m, op["direction"])
    length = float(op["length"])
    vec = gp_Vec(d).Scaled(length)
    if op.get("symmetric", False):
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
        from OCP.gp import gp_Trsf
        tr = gp_Trsf()
        tr.SetTranslation(gp_Vec(d).Scaled(-length))
        profile = BRepBuilderAPI_Transform(profile, tr, True).Shape()
        vec = gp_Vec(d).Scaled(2 * length)
    return BRepPrimAPI_MakePrism(profile, vec).Shape()


def sweep(m: Model, op: dict) -> TopoDS_Shape:
    """輪郭（稜線列）をパス（稜線列）に沿って掃引した面。"""
    profile = _wire(m, op["profile"])
    path = _wire(m, op["path"])
    return BRepOffsetAPI_MakePipe(path, profile).Shape()


def patch(m: Model, op: dict) -> TopoDS_Shape:
    """稜線のループを埋める n 辺パッチ。平面ループなら平面、そうでなければ MakeFilling。"""
    edges = [TopoDS.Edge(m.ids[e]) for e in op["edges"]]
    mw = BRepBuilderAPI_MakeWire()
    for e in edges:
        mw.Add(e)
    if mw.IsDone():
        mf = BRepBuilderAPI_MakeFace(mw.Wire(), True)
        if mf.IsDone():
            return mf.Face()
    return _fill(edges)


def project(m: Model, op: dict) -> TopoDS_Shape:
    """稜線を面へ方向投影した稜線（面を割る道具にするには、さらに押し出して板にする）。"""
    wire = _wire(m, op["edges"])
    face = TopoDS.Face(m.ids[op["face"]])
    d = refs.direction(m, op["direction"]) if "direction" in op else refs.direction(m, {"normal": op["face"]})
    proj = BRepProj_Projection(wire, face, d)
    if not proj.IsDone():
        raise ValueError(f"{op['id']}: projection failed")
    return proj.Shape()


# ---- 分割 ---------------------------------------------------------------

def partition(m: Model, op: dict) -> Model:
    tools = op["tool"] if isinstance(op["tool"], list) else [op["tool"]]
    shapes = [m.ids[t] for t in tools]
    tool = shapes[0] if len(shapes) == 1 else _compound(shapes)
    return m.partition_with(op["id"], tool, _side_for(tool))


def split_edge(m: Model, op: dict) -> Model:
    p = refs.point(m, {"on": op["edge"], "t": float(op.get("t", 0.5))}) if "t" in op or "point" not in op else refs.point(m, op["point"])
    v = BRepBuilderAPI_MakeVertex(p).Vertex()
    return m.partition_with(op["id"], v, lambda s: 0.0)


def _side_for(tool: TopoDS_Shape):
    """兄弟順序の規則を道具から決める: 平面は法線の正側、円筒は内側、その他は重心順（side=0）。"""
    faces = list(_sub(tool, TopAbs_FACE))
    if len(faces) == 1:
        surf = BRepAdaptor_Surface(TopoDS.Face(faces[0]))
        t = surf.GetType()
        if t == GeomAbs_Plane:
            pln = surf.Plane()
            o, n = pln.Location(), pln.Axis().Direction()
            return lambda s: gp_Vec(o, gp_Pnt(*centroid(s))).Dot(gp_Vec(n))
        if t == GeomAbs_Cylinder:
            cyl = surf.Cylinder()
            o, a, r = cyl.Location(), cyl.Axis().Direction(), cyl.Radius()

            def side(s):
                v = gp_Vec(o, gp_Pnt(*centroid(s)))
                t_ = v.Dot(gp_Vec(a))
                return r - (v - gp_Vec(a).Scaled(t_)).Magnitude()

            return side
    return lambda s: 0.0


# ---- 補助 ---------------------------------------------------------------

def _sub(shape: TopoDS_Shape, kind):
    ex = TopExp_Explorer(shape, kind)
    while ex.More():
        yield ex.Current()
        ex.Next()


def _compound(shapes) -> TopoDS_Compound:
    comp = TopoDS_Compound()
    b = BRep_Builder()
    b.MakeCompound(comp)
    for s in shapes:
        b.Add(comp, s)
    return comp


def _wire(m: Model, ids) -> TopoDS_Shape:
    ids = ids if isinstance(ids, list) else [ids]
    mw = BRepBuilderAPI_MakeWire()
    for lid in ids:
        s = m.ids[lid]
        if s.ShapeType() == TopAbs_EDGE:
            mw.Add(TopoDS.Edge(s))
        elif s.ShapeType() == TopAbs_WIRE:
            mw.Add(TopoDS.Wire(s))
        else:  # 稜線の集まり（スケッチの compound）
            for e in _sub(s, TopAbs_EDGE):
                mw.Add(TopoDS.Edge(e))
    if not mw.IsDone():
        raise ValueError(f"edges {ids} do not form a connected wire")
    return mw.Wire()


def _profile(m: Model, lid: str) -> TopoDS_Shape:
    s = m.ids[lid]
    if s.ShapeType() in (TopAbs_FACE, TopAbs_EDGE, TopAbs_WIRE):
        return s
    return _wire(m, [lid])


def _fill(edges) -> TopoDS_Shape:
    f = BRepOffsetAPI_MakeFilling()
    for e in edges:
        f.Add(TopoDS.Edge(e), GeomAbs_C0, True)
    f.Build()
    if not f.IsDone():
        raise ValueError("n-sided patch failed")
    return f.Shape()


# ---- 操作の登録 -----------------------------------------------------------

TOOL_OPS = {
    "datum_plane": datum_plane,
    "extend_face": extend_face,
    "sketch": sketch,
    "extrude": extrude,
    "sweep": sweep,
    "patch": patch,
    "project": project,
}
PARTITION_OPS = {
    "partition": partition,
    "split_edge": split_edge,
}


def apply(m: Model, op: dict) -> Model:
    kind = op["type"]
    if kind in TOOL_OPS:
        return m.add_tool(op["id"], TOOL_OPS[kind](m, op))
    if kind in PARTITION_OPS:
        return PARTITION_OPS[kind](m, op)
    # 糖衣: 平面・円筒は道具を暗黙に作って分割する（道具の ID は付かない）
    if kind == "plane":
        return m.partition_plane(op["id"], op["origin"], op["normal"], op.get("fuzzy", 0.0))
    if kind == "cylinder":
        return m.partition_cylinder(op["id"], op["origin"], op["axis"], op["radius"], op.get("seam", (1.0, 0.0, 0.0)), op.get("fuzzy", 0.0))
    raise ValueError(f"unknown op type: {kind}")
