"""OCC まわりの薄いユーティリティ。形状は読むだけで、ここでは決して編集しない。"""
from __future__ import annotations

import hashlib

from OCP.BRep import BRep_Tool
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID, TopAbs_VERTEX
from OCP.TopExp import TopExp
from OCP.TopoDS import TopoDS, TopoDS_Shape
from OCP.collections import IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as IndexedMapOfShape

# 系譜IDの種別記号。S=ソリッド, F=面, E=稜線, V=頂点
KINDS: dict[str, int] = {"S": TopAbs_SOLID, "F": TopAbs_FACE, "E": TopAbs_EDGE, "V": TopAbs_VERTEX}
KIND_DIM: dict[str, int] = {"S": 3, "F": 2, "E": 1, "V": 0}


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_step(path: str) -> TopoDS_Shape:
    reader = STEPControl_Reader()
    if reader.ReadFile(path) != 1:
        raise RuntimeError(f"STEP read failed: {path}")
    reader.TransferRoots()
    return reader.OneShape()


def explore(shape: TopoDS_Shape, kind: str) -> list[TopoDS_Shape]:
    """TopExp の順でサブ形状を列挙する。この順序が初期IDの番号になる。"""
    m = IndexedMapOfShape()
    TopExp.MapShapes_s(shape, KINDS[kind], m)
    return [m.FindKey(i) for i in range(1, m.Extent() + 1)]


def centroid(s: TopoDS_Shape) -> tuple[float, float, float]:
    """重心。兄弟の順序付けと検算にだけ使う（照合には使わない）。"""
    t = s.ShapeType()
    if t == TopAbs_VERTEX:
        p = BRep_Tool.Pnt_s(TopoDS.Vertex(s))
        return (p.X(), p.Y(), p.Z())
    props = GProp_GProps()
    if t == TopAbs_SOLID:
        BRepGProp.VolumeProperties_s(s, props)
    elif t == TopAbs_FACE:
        BRepGProp.SurfaceProperties_s(s, props)
    else:
        BRepGProp.LinearProperties_s(s, props)
    c = props.CentreOfMass()
    return (c.X(), c.Y(), c.Z())
