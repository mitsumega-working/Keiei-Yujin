"""系譜ID と Gmsh のエンティティタグの対応。

Gmsh は BREP を読むと TopExp と同じ順でタグを振る（検算付きでその前提に乗る）。
幾何照合はしない。順序がずれていれば例外にする。
"""
from __future__ import annotations

import os
import tempfile

import gmsh
from OCP.BRepTools import BRepTools

from .geometry import KIND_DIM, centroid, explore
from .lineage import Model


def load_model(m: Model, check: bool = True) -> dict[str, tuple[int, int]]:
    """gmsh.initialize() 済みの前提で Model を取り込み、系譜ID -> (dim, tag) を返す。"""
    fd, path = tempfile.mkstemp(suffix=".brep")
    os.close(fd)
    try:
        BRepTools.Write_s(m.shape, path)
        gmsh.model.occ.importShapes(path)
        gmsh.model.occ.synchronize()
    finally:
        os.unlink(path)

    out: dict[str, tuple[int, int]] = {}
    for kind, dim in KIND_DIM.items():
        ours = explore(m.shape, kind)
        tags = [t for _, t in gmsh.model.getEntities(dim)]
        if len(ours) != len(tags):
            raise RuntimeError(f"entity count mismatch for {kind}: occ={len(ours)} gmsh={len(tags)}")
        for s, tag in zip(ours, tags):
            if check:
                c_ours = centroid(s)
                c_gmsh = gmsh.model.getValue(0, tag, []) if dim == 0 else gmsh.model.occ.getCenterOfMass(dim, tag)
                if max(abs(a - b) for a, b in zip(c_ours, c_gmsh)) > 1e-6:
                    raise RuntimeError(f"entity order mismatch at {kind} tag {tag}: {c_ours} vs {c_gmsh}")
            lid = m.id_of(s)
            if lid is not None:
                out[lid] = (dim, tag)
    return out
