"""メッシュ生成と FrontISTR writer: 整合、向き、SGROUP の側指定。"""
import collections
import os
import re
from itertools import combinations

import numpy as np
import pytest

from keiei_yujin import fistr, lineage, mesh
from keiei_yujin.geometry import sha256_of

HERE = os.path.dirname(__file__)
STEP = os.path.join(HERE, "data", "part.step")


def read_fistr(path):
    nodes, elems, cur, sgname = {}, {}, None, None
    sg = collections.defaultdict(list)
    for ln in open(path):
        if ln.startswith("!"):
            cur = ln
            m = re.search(r"SGRP=(\w+)", ln)
            sgname = m.group(1) if m else None
            continue
        if not ln.strip():
            continue
        v = [x.strip() for x in ln.split(",")]
        if cur.startswith("!NODE"):
            nodes[int(v[0])] = tuple(float(x) for x in v[1:])
        elif cur.startswith("!ELEMENT"):
            etype = int(re.search(r"TYPE=(\d+)", cur).group(1))
            elems[int(v[0])] = (etype, tuple(int(x) for x in v[1:]))
        elif cur.startswith("!SGROUP"):
            sg[sgname].append((int(v[0]), int(v[1])))
    return nodes, elems, sg


def tet_volume(nodes, nd):
    p = np.array([nodes[i] for i in nd])
    return np.linalg.det(p[1:] - p[0]) / 6


def hex_jacobian_sign(nodes, nd):
    p = np.array([nodes[i] for i in nd])
    # 節点1での辺ベクトル (1->2, 1->4, 1->5) の三重積
    return np.linalg.det(np.array([p[1] - p[0], p[3] - p[0], p[4] - p[0]]))


@pytest.fixture
def base_log():
    return {
        "step": {"path": STEP, "sha256": sha256_of(STEP)},
        "ops": [
            {"id": "P1", "type": "plane", "origin": [8, 0, 0], "normal": [1, 0, 0]},
            {"id": "P2", "type": "plane", "origin": [14, 0, 0], "normal": [1, 0, 0]},
        ],
    }


def test_tet_mesh_is_conformal_and_oriented(base_log, tmp_path):
    m = lineage.replay(base_log)
    groups = fistr.Groups.from_dict({
        "volumes": {"left": ["S1.2"], "mid": ["S1.1.2"], "right": ["S1.1.1"]},
        "surfaces": {
            "fix_end": ["F1"],
            "cut_P1": {"faces": ["P1.F1"], "side": "S1.2"},  # 内部面は側を指定
        },
        "nodes": {"corner": ["P1.V1"], "cut_edges": ["P1.E1", "P1.E2", "P1.E3", "P1.E4"]},
    })
    out = tmp_path / "tet.msh"
    stats = mesh.generate(m, mesh.MeshSettings(size=2.0), groups, str(out))
    nodes, elems, sg = read_fistr(out)
    assert stats["elements"] == len(elems) and set(stats["egroups"]) == {"left", "mid", "right"}
    # 重複節点なし（界面が共有されている）
    coords = collections.Counter(tuple(round(c, 6) for c in p) for p in nodes.values())
    assert max(coords.values()) == 1
    # 内部面 cut_P1 の三角形は両側の要素に共有され、SGROUP は side のソリッド (x<8) 側に書かれる
    faces = collections.defaultdict(list)
    for eid, (et, nd) in elems.items():
        for fno, loc in fistr.FACE_TABLES[et].items():
            faces[tuple(sorted(nd[l - 1] for l in loc))].append(eid)
    for eid, fno in sg["cut_P1"]:
        key = tuple(sorted(elems[eid][1][l - 1] for l in fistr.FACE_TABLES[341][fno]))
        assert len(faces[key]) == 2
        assert max(nodes[n][0] for n in elems[eid][1]) <= 8 + 1e-9
    # 外周面 fix_end は片側だけ
    for eid, fno in sg["fix_end"]:
        key = tuple(sorted(elems[eid][1][l - 1] for l in fistr.FACE_TABLES[341][fno]))
        assert len(faces[key]) == 1
    # 向き: 負体積なし
    assert all(tet_volume(nodes, nd) > 0 for et, nd in elems.values())
    assert stats["ngroups"]["corner"] == 1


def test_internal_face_without_side_is_rejected(base_log, tmp_path):
    m = lineage.replay(base_log)
    groups = fistr.Groups.from_dict({
        "volumes": {"all": ["S1.2", "S1.1.2", "S1.1.1"]},
        "surfaces": {"cut_P1": ["P1.F1"]},
    })
    with pytest.raises(ValueError, match="side"):
        mesh.generate(m, mesh.MeshSettings(size=2.0), groups, str(tmp_path / "x.msh"))


def test_ogrid_partition_gives_all_hex(base_log, tmp_path):
    """穴の軸を通る平面で O-grid 分割すると全ソリッドが6面になり、transfinite 自動で全ヘキサになる。"""
    log = dict(base_log)
    log["ops"] = base_log["ops"] + [
        {"id": "P3", "type": "plane", "origin": [14, 0, 0], "normal": [0, 1, 0]},
        {"id": "P4", "type": "plane", "origin": [14, 0, 0], "normal": [-5, 6, 0]},
        {"id": "P5", "type": "plane", "origin": [14, 0, 0], "normal": [5, 6, 0]},
        {"id": "P6", "type": "cylinder", "origin": [14, 0, 0], "axis": [0, 0, 1], "radius": 3.5, "seam": [1, 0, 0]},
    ]
    m = lineage.replay(log)
    from keiei_yujin.geometry import explore
    assert all(len(explore(m.ids[s], "F")) == 6 for s in m.entities("S"))
    # リングの放射方向の稜線 = P6 が割った稜線の内側の子
    radial = [k for k, op in m.created_by.items() if op == "P6" and lineage.kind_of(k) == "E" and k.endswith(".1")]
    assert len(radial) == 16
    groups = fistr.Groups.from_dict({"volumes": {"all": m.entities("S")}})
    settings = mesh.MeshSettings(size=1.5, transfinite_auto=True, seeds={e: 2 for e in radial})
    out = tmp_path / "hex.msh"
    stats = mesh.generate(m, settings, groups, str(out))
    nodes, elems, _ = read_fistr(out)
    assert {et for et, _ in elems.values()} == {361}
    assert list(stats["quality"]) == [5]  # Gmsh hex8 のみ
    assert all(hex_jacobian_sign(nodes, nd) > 0 for _, nd in elems.values())
