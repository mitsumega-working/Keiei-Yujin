"""FrontISTR メッシュ (!NODE / !ELEMENT / !SGROUP / !NGROUP) の writer。

面番号表は FrontISTR の fistr1/src/lib/element/element.f90 `getSubFace` に合わせる
（角節点だけを使う。2次要素の中間節点の対応は未検証のため、現状は1次のみ）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import gmsh

# FrontISTR 要素タイプ -> (Gmsh 要素タイプ, 節点数, 面番号 -> 局所節点番号(1始まり))
FACE_TABLES: dict[int, dict[int, tuple[int, ...]]] = {
    341: {1: (1, 2, 3), 2: (4, 2, 1), 3: (4, 3, 2), 4: (4, 1, 3)},
    361: {1: (1, 2, 3, 4), 2: (8, 7, 6, 5), 3: (5, 6, 2, 1), 4: (6, 7, 3, 2), 5: (7, 8, 4, 3), 6: (8, 5, 1, 4)},
    351: {1: (1, 2, 3), 2: (6, 5, 4), 3: (4, 5, 2, 1), 4: (5, 6, 3, 2), 5: (6, 4, 1, 3)},
}
GMSH_TO_FISTR: dict[int, int] = {4: 341, 5: 361, 6: 351}  # Gmsh tet4 / hex8 / prism6
GMSH_NNODES: dict[int, int] = {4: 4, 5: 8, 6: 6, 7: 5}


@dataclass
class SurfaceGroup:
    name: str
    faces: list[str]  # 系譜ID
    side: list[str] | None = None  # 内部面のとき、どちら側の要素で書くか（ソリッドの系譜ID、複数可）


@dataclass
class Groups:
    volumes: dict[str, list[str]] = field(default_factory=dict)  # EGRP 名 -> ソリッドID
    surfaces: list[SurfaceGroup] = field(default_factory=list)
    nodes: dict[str, list[str]] = field(default_factory=dict)  # NGRP 名 -> エンティティID（同一次元）

    @classmethod
    def from_dict(cls, d: dict) -> "Groups":
        g = cls()
        g.volumes = dict(d.get("volumes", {}))
        for name, spec in d.get("surfaces", {}).items():
            if isinstance(spec, list):
                g.surfaces.append(SurfaceGroup(name, list(spec)))
            else:
                side = spec.get("side")
                if isinstance(side, str):
                    side = [side]
                g.surfaces.append(SurfaceGroup(name, list(spec["faces"]), side))
        g.nodes = dict(d.get("nodes", {}))
        return g


def _elements_of_volume(vtag: int) -> dict[int, tuple[int, tuple[int, ...]]]:
    """gmsh の体積タグ -> {要素ID: (FrontISTR タイプ, 節点)}"""
    out = {}
    etypes, etags, enodes = gmsh.model.mesh.getElements(3, vtag)
    for et, tg, nd in zip(etypes, etags, enodes):
        et = int(et)
        if et not in GMSH_TO_FISTR:
            raise ValueError(f"unsupported Gmsh element type {et} (pyramid etc.)")
        n = GMSH_NNODES[et]
        ft = GMSH_TO_FISTR[et]
        for i, t in enumerate(tg):
            out[int(t)] = (ft, tuple(int(x) for x in nd[n * i : n * i + n]))
    return out


def write(path: str, groups: Groups, tagmap: dict[str, tuple[int, int]], header: str = "keiei-yujin") -> dict:
    """gmsh.model.mesh.generate 済みのメッシュを FrontISTR 形式で書く。"""
    ntags, coords, _ = gmsh.model.mesh.getNodes()
    nodes = {int(t): coords[3 * i : 3 * i + 3] for i, t in enumerate(ntags)}

    # 要素と EGRP
    elems: dict[int, tuple[int, tuple[int, ...]]] = {}
    egrp: dict[str, list[int]] = {}
    elems_in_solid: dict[str, set[int]] = {}
    for gname, sids in groups.volumes.items():
        egrp[gname] = []
        for sid in sids:
            part = _elements_of_volume(tagmap[sid][1])
            elems.update(part)
            egrp[gname].extend(part)
            elems_in_solid.setdefault(sid, set()).update(part)

    # 面 -> [(要素, 面番号)]
    face_index: dict[tuple[int, ...], list[tuple[int, int]]] = {}
    for eid, (ft, nd) in elems.items():
        for fno, loc in FACE_TABLES[ft].items():
            key = tuple(sorted(nd[l - 1] for l in loc))
            face_index.setdefault(key, []).append((eid, fno))

    sgrp: dict[str, list[tuple[int, int]]] = {}
    for sg in groups.surfaces:
        allowed = None
        if sg.side is not None:
            allowed = set().union(*(elems_in_solid[s] for s in sg.side))
        pairs: list[tuple[int, int]] = []
        for fid in sg.faces:
            stag = tagmap[fid][1]
            etypes, etags, enodes = gmsh.model.mesh.getElements(2, stag)
            for et, tg, nd in zip(etypes, etags, enodes):
                n = {2: 3, 3: 4}[int(et)]
                for i in range(len(tg)):
                    key = tuple(sorted(int(x) for x in nd[n * i : n * i + n]))
                    owners = face_index.get(key, [])
                    if allowed is not None:
                        owners = [o for o in owners if o[0] in allowed]
                    if len(owners) == 0:
                        raise ValueError(f"SGROUP {sg.name}: face {fid} is not bounded by the `side` solids {sg.side}")
                    if len(owners) > 1:
                        raise ValueError(f"SGROUP {sg.name}: face {fid} is an internal face; specify `side`")
                    pairs.append(owners[0])
        sgrp[sg.name] = pairs

    ngrp: dict[str, list[int]] = {}
    for gname, ids in groups.nodes.items():
        acc: set[int] = set()
        for eid_ in ids:
            dim, tag = tagmap[eid_]
            nt = gmsh.model.mesh.getNodes(dim, tag, includeBoundary=True)[0]
            acc.update(int(x) for x in nt)
        ngrp[gname] = sorted(acc)

    with open(path, "w") as f:
        f.write(f"!HEADER\n {header}\n!NODE\n")
        for t in sorted(nodes):
            x, y, z = nodes[t]
            f.write(f" {t}, {x:.10g}, {y:.10g}, {z:.10g}\n")
        for gname, eids in egrp.items():
            by_type: dict[int, list[int]] = {}
            for eid in eids:
                by_type.setdefault(elems[eid][0], []).append(eid)
            for ft, lst in by_type.items():
                f.write(f"!ELEMENT, TYPE={ft}, EGRP={gname}\n")
                for eid in lst:
                    f.write(f" {eid}, " + ", ".join(str(n) for n in elems[eid][1]) + "\n")
        for gname, pairs in sgrp.items():
            f.write(f"!SGROUP, SGRP={gname}\n")
            for eid, fno in pairs:
                f.write(f" {eid}, {fno}\n")
        for gname, nids in ngrp.items():
            f.write(f"!NGROUP, NGRP={gname}\n")
            for i in range(0, len(nids), 10):
                f.write(" " + ", ".join(map(str, nids[i : i + 10])) + "\n")
        f.write("!END\n")

    return {
        "nodes": len(nodes),
        "elements": len(elems),
        "egroups": {k: len(v) for k, v in egrp.items()},
        "sgroups": {k: len(v) for k, v in sgrp.items()},
        "ngroups": {k: len(v) for k, v in ngrp.items()},
    }
