"""生成操作（道具）と道具による分割。Abaqus/CAE のパーティション方法の対応（docs/abaqus-partition-coverage.md）。"""
import math
import os

import pytest

from keiei_yujin import fistr, lineage, mesh
from keiei_yujin.geometry import sha256_of

HERE = os.path.dirname(__file__)
STEP = os.path.join(HERE, "data", "part.step")  # 20x10x10 の箱 (x 0..20, y,z -5..5)、x=14 に半径2の貫通穴 (z 方向)


def run(ops):
    return lineage.replay({"step": {"path": STEP, "sha256": sha256_of(STEP)}, "ops": ops})


# ---- 基準面 ---------------------------------------------------------------

def test_datum_plane_point_normal_with_id_refs():
    # 穴の中心を通り、穴の軸に垂直な面 (z=0)
    m = run([
        {"id": "D1", "type": "datum_plane", "point": {"center": "F7"}, "normal": {"axis": "F7"}},
        {"id": "P1", "type": "partition", "tool": "D1.F1"},
    ])
    assert m.entities("S") == ["S1.1", "S1.2"]
    assert lineage.id_table(m)["P1.F1"][2] == pytest.approx(0.0)
    assert "D1.F1" in m.tool_ids() and "D1.F1" not in m.entities("F")


def test_datum_plane_three_points_and_offset_face():
    m = run([
        {"id": "D1", "type": "datum_plane", "points": [[8, 0, 0], {"vertex": "V1"}, {"midpoint": "E2"}]},
    ])
    assert "D1.F1" in m.ids
    m = run([
        {"id": "D2", "type": "datum_plane", "offset_face": "F1", "distance": -3},  # F1 は x=0 の端面、法線は外向き (-x)
        {"id": "P1", "type": "partition", "tool": "D2.F1"},
    ])
    assert lineage.id_table(m)["P1.F1"][0] == pytest.approx(3.0)


# ---- 面の延長 -------------------------------------------------------------

def test_extend_face_of_cylinder_is_a_full_cylinder():
    m = run([{"id": "X1", "type": "extend_face", "face": "F7"}])
    c = lineage.id_table(m)["X1.F1"]
    assert c[0] == pytest.approx(14.0) and c[1] == pytest.approx(0.0)


# ---- スケッチ → 押し出し → 分割 ----------------------------------------------

def test_sketch_circle_extrude_sheet_partition_ring():
    m = run([
        {"id": "K1", "type": "sketch", "closed": True,
         "elements": [{"circle": {"center": {"center": "F7"}, "radius": 3.5, "normal": {"axis": "F7"}}}]},
        {"id": "T1", "type": "extrude", "profile": "K1.E1", "direction": {"axis": "F7"}, "length": 20, "symmetric": True},
        {"id": "P1", "type": "partition", "tool": "T1.F1"},
    ])
    assert m.entities("S") == ["S1.1", "S1.2"]  # 円筒は内側が .1
    c = lineage.id_table(m)
    assert math.hypot(c["S1.1"][0] - 14, c["S1.1"][1]) < 3.5
    assert c["P1.F1"][0] == pytest.approx(14.0)


def test_sketch_rect_extrude_solid_partition_pocket():
    m = run([
        {"id": "K2", "type": "sketch", "closed": True,
         "elements": [{"polyline": [[2, -2, 6], [6, -2, 6], [6, 2, 6], [2, 2, 6], [2, -2, 6]]}]},
        {"id": "T2", "type": "extrude", "profile": "K2.F1", "direction": [0, 0, -1], "length": 3},
        {"id": "P1", "type": "partition", "tool": "T2.S1"},
    ])
    assert m.entities("S") == ["S1.1", "S1.2"]
    # 4 側面 + 底面 (z=3)。道具の面がそのまま結果に残っても操作の子として扱う
    assert [k for k in m.ids if k.startswith("P1.F")] == ["P1.F1", "P1.F2", "P1.F3", "P1.F4", "P1.F5"]
    assert lineage.id_table(m)["P1.F5"][2] == pytest.approx(3.0)
    assert {"F3.1", "F3.2"} <= set(m.ids)  # 上面は割られる


# ---- スイープ、n 辺パッチ、投影 -------------------------------------------

def test_sweep_edge_along_spline_path():
    m = run([
        {"id": "K3", "type": "sketch", "elements": [{"line": [[0, 0, -5], [20, 0, -5]]}]},
        {"id": "K4", "type": "sketch", "elements": [{"spline": [[0, 0, -5], [0, 1.5, 0], [0, 0, 5]]}]},
        {"id": "W1", "type": "sweep", "profile": "K3.E1", "path": "K4.E1"},
        {"id": "P1", "type": "partition", "tool": "W1.F1"},
    ])
    assert len(m.entities("S")) == 2


def test_nonplanar_loop_becomes_patch():
    m = run([
        {"id": "K5", "type": "sketch", "closed": True,
         "elements": [{"polyline": [[0, -5, 0], [20, -5, 2], [20, 5, 0], [0, 5, -2], [0, -5, 0]]}]},
        {"id": "P1", "type": "partition", "tool": "K5.F1"},
    ])
    assert len(m.entities("S")) == 2


def test_project_arc_onto_face_and_imprint():
    m = run([
        {"id": "K6", "type": "sketch", "elements": [{"arc": [[2, -4, 9], [6, 0, 9], [2, 4, 9]]}]},
        {"id": "J1", "type": "project", "edges": ["K6.E1"], "face": "F3", "direction": [0, 0, -1]},
        {"id": "T3", "type": "extrude", "profile": "J1.E1", "direction": [0, 0, 1], "length": 0.5, "symmetric": True},
        {"id": "P1", "type": "partition", "tool": "T3.F1"},
    ])
    assert lineage.id_table(m)["J1.E1"][2] == pytest.approx(5.0)  # 上面 z=5 に投影された
    assert m.entities("S") == ["S1"]  # 割られていない
    assert "F3" in m.ids  # 刻まれただけの面は ID を保つ
    assert "P1.F1" in m.ids  # 内部面（埋め込み）


# ---- 辺の分割 -------------------------------------------------------------

def test_split_edge_by_parameter():
    m = run([{"id": "Q1", "type": "split_edge", "edge": "E5", "t": 0.25}])
    assert {"E5.1", "E5.2", "Q1.V1"} <= set(m.ids)
    assert m.entities("S") == ["S1"] and "F2" in m.ids  # 触れた面・ソリッドは ID を保つ


# ---- 道具があってもメッシュまで通る ----------------------------------------------

def test_mesh_with_tools_present(tmp_path):
    m = run([
        {"id": "D1", "type": "datum_plane", "point": [8, 0, 0], "normal": [1, 0, 0]},
        {"id": "P1", "type": "partition", "tool": "D1.F1"},
    ])
    groups = fistr.Groups.from_dict({"volumes": {"a": ["S1.1"], "b": ["S1.2"]}, "surfaces": {"cut": {"faces": ["P1.F1"], "side": "S1.2"}}})
    stats = mesh.generate(m, mesh.MeshSettings(size=2.5), groups, str(tmp_path / "m.msh"))
    assert stats["sgroups"]["cut"] > 0


def test_tool_ids_cannot_be_referenced_from_mesh_settings(tmp_path):
    m = run([{"id": "D1", "type": "datum_plane", "point": [8, 0, 0], "normal": [1, 0, 0]}])
    groups = fistr.Groups.from_dict({"volumes": {"a": ["S1"]}, "surfaces": {"x": ["D1.F1"]}})
    with pytest.raises(mesh.BrokenReference, match="道具"):
        mesh.generate(m, mesh.MeshSettings(size=2.5), groups, str(tmp_path / "m.msh"))
