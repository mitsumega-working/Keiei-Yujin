"""点・方向の指定を解決する。座標の代わりに系譜IDを参照できる。

点:   [x, y, z]
      {"vertex": "V3"}            頂点
      {"midpoint": "E12"}         稜線の中点（パラメータ中央）
      {"on": "E12", "t": 0.3}     稜線上のパラメータ位置 (0..1)
      {"center": "F8"}            面・ソリッドの重心
方向: [x, y, z]
      {"normal": "F8"}            面の法線（曲面は重心付近の法線）
      {"tangent": "E12"}          稜線の接線（始点）
      {"axis": "F7"}              円筒・円錐面の軸
      {"from": 点, "to": 点}      2点を結ぶ向き
"""
from __future__ import annotations

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepLProp import BRepLProp_SLProps
from OCP.GeomAbs import GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Plane
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Dir, gp_Pnt, gp_Vec

from .geometry import centroid


def _shape(m, lid: str):
    try:
        return m.ids[lid]
    except KeyError:
        raise KeyError(f"系譜ID {lid} は存在しません") from None


def point(m, spec) -> gp_Pnt:
    if isinstance(spec, (list, tuple)):
        return gp_Pnt(*map(float, spec))
    if "vertex" in spec:
        return BRep_Tool.Pnt_s(TopoDS.Vertex(_shape(m, spec["vertex"])))
    if "midpoint" in spec or "on" in spec:
        lid = spec.get("midpoint") or spec["on"]
        t = 0.5 if "midpoint" in spec else float(spec.get("t", 0.5))
        c = BRepAdaptor_Curve(TopoDS.Edge(_shape(m, lid)))
        u = c.FirstParameter() + t * (c.LastParameter() - c.FirstParameter())
        return c.Value(u)
    if "center" in spec:
        return gp_Pnt(*centroid(_shape(m, spec["center"])))
    raise ValueError(f"unknown point spec: {spec}")


def direction(m, spec) -> gp_Dir:
    if isinstance(spec, (list, tuple)):
        return gp_Dir(*map(float, spec))
    if "normal" in spec:
        face = TopoDS.Face(_shape(m, spec["normal"]))
        surf = BRepAdaptor_Surface(face)
        u = 0.5 * (surf.FirstUParameter() + surf.LastUParameter())
        v = 0.5 * (surf.FirstVParameter() + surf.LastVParameter())
        props = BRepLProp_SLProps(surf, u, v, 1, 1e-9)
        if not props.IsNormalDefined():
            raise ValueError(f"face {spec['normal']}: normal not defined")
        n = props.Normal()
        from OCP.TopAbs import TopAbs_REVERSED
        if face.Orientation() == TopAbs_REVERSED:
            n.Reverse()
        return n
    if "tangent" in spec:
        c = BRepAdaptor_Curve(TopoDS.Edge(_shape(m, spec["tangent"])))
        p, v = gp_Pnt(), gp_Vec()
        c.D1(c.FirstParameter(), p, v)
        return gp_Dir(v)
    if "axis" in spec:
        surf = BRepAdaptor_Surface(TopoDS.Face(_shape(m, spec["axis"])))
        t = surf.GetType()
        if t == GeomAbs_Cylinder:
            return surf.Cylinder().Axis().Direction()
        if t == GeomAbs_Cone:
            return surf.Cone().Axis().Direction()
        if t == GeomAbs_Plane:
            return surf.Plane().Axis().Direction()
        raise ValueError(f"face {spec['axis']} has no axis (surface type {t})")
    if "from" in spec and "to" in spec:
        a, b = point(m, spec["from"]), point(m, spec["to"])
        return gp_Dir(gp_Vec(a, b))
    raise ValueError(f"unknown direction spec: {spec}")


def as_tuple(p) -> tuple[float, float, float]:
    return (p.X(), p.Y(), p.Z())
