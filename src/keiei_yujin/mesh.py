"""メッシュ設定（形状は触らず、系譜IDを参照するだけ）を Gmsh に適用して切る。"""
from __future__ import annotations

from dataclasses import dataclass, field

import gmsh

from . import fistr, gmsh_bridge
from .lineage import Model

ALGO3D = {"delaunay": 1, "frontal": 4, "hxt": 10, "mmg3d": 7, "rtree": 9}


@dataclass
class MeshSettings:
    size: float = 1.0
    order: int = 1
    algorithm3d: str = "delaunay"
    optimize_netgen: bool = True
    transfinite_auto: bool = False  # 6面体ソリッドを自動で構造格子にし、recombine する
    seeds: dict[str, int] = field(default_factory=dict)  # 稜線の系譜ID -> 節点数

    @classmethod
    def from_dict(cls, d: dict) -> "MeshSettings":
        return cls(
            size=float(d.get("size", 1.0)),
            order=int(d.get("order", 1)),
            algorithm3d=d.get("algorithm3d", "delaunay"),
            optimize_netgen=bool(d.get("optimize_netgen", True)),
            transfinite_auto=bool(d.get("transfinite_auto", False)),
            seeds={k: int(v) for k, v in d.get("seeds", {}).items()},
        )


class BrokenReference(ValueError):
    """メッシュ設定やグループが、現在の再生結果に存在しない系譜IDを指している。"""


def check_references(m: Model, settings: MeshSettings, groups: fistr.Groups) -> None:
    refs: list[tuple[str, str]] = []
    refs += [("mesh.seeds", k) for k in settings.seeds]
    refs += [(f"groups.volumes.{g}", i) for g, ids in groups.volumes.items() for i in ids]
    refs += [(f"groups.surfaces.{sg.name}", i) for sg in groups.surfaces for i in sg.faces]
    refs += [(f"groups.surfaces.{sg.name}.side", s) for sg in groups.surfaces for s in (sg.side or [])]
    refs += [(f"groups.nodes.{g}", i) for g, ids in groups.nodes.items() for i in ids]
    problems = []
    tool_ids = m.tool_ids()
    for where, lid in refs:
        if lid in tool_ids:
            problems.append(f"{where}: {lid} は道具のエンティティです（メッシュ設定・グループからは参照できません）")
            continue
        if lid in m.ids:
            continue
        kids = [k for k in m.ids if k.startswith(lid + ".")]
        hint = f"（分割されています: {', '.join(kids)}）" if kids else ""
        problems.append(f"{where}: {lid} は存在しません{hint}")
    if problems:
        raise BrokenReference("\n".join(problems))


def generate(m: Model, settings: MeshSettings, groups: fistr.Groups, out_path: str, keep_gmsh: str | None = None) -> dict:
    """Model を Gmsh に渡してメッシュを切り、FrontISTR 形式で書く。統計を返す。"""
    if settings.order != 1:
        raise NotImplementedError("2次要素の中間節点順序は未検証（docs/design.md 未検証項目）")
    check_references(m, settings, groups)
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        tagmap = gmsh_bridge.load_model(m)
        gmsh.option.setNumber("Mesh.MeshSizeMax", settings.size)
        gmsh.option.setNumber("Mesh.Algorithm3D", ALGO3D[settings.algorithm3d])
        gmsh.option.setNumber("Mesh.Optimize", 1)
        gmsh.option.setNumber("Mesh.OptimizeNetgen", 1 if settings.optimize_netgen else 0)
        if settings.transfinite_auto:
            gmsh.model.mesh.setTransfiniteAutomatic(recombine=True)
        for eid, n in settings.seeds.items():
            dim, tag = tagmap[eid]
            if dim != 1:
                raise ValueError(f"seed target {eid} is not an edge")
            gmsh.model.mesh.setTransfiniteCurve(tag, n)
        gmsh.model.mesh.generate(3)

        stats = fistr.write(out_path, groups, tagmap)
        types, tags, _ = gmsh.model.mesh.getElements(3)
        quality = {}
        for t, tg in zip(types, tags):
            measure = "gamma" if int(t) == 4 else "minSJ"  # gamma はテトラ用、ヘキサ等は最小スケールドヤコビアン
            q = gmsh.model.mesh.getElementQualities(tg, measure)
            quality[int(t)] = {"count": len(tg), "measure": measure, "min": float(min(q)), "mean": float(sum(q) / len(q))}
        stats["quality"] = quality
        if keep_gmsh:
            gmsh.write(keep_gmsh)
        return stats
    finally:
        gmsh.finalize()
