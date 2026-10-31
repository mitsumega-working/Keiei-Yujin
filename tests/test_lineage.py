"""系譜IDの規則と、再生の決定性。"""
import json
import os
import subprocess
import sys

import pytest

from keiei_yujin import lineage
from keiei_yujin.geometry import sha256_of

HERE = os.path.dirname(__file__)
STEP = os.path.join(HERE, "data", "part.step")  # 20x10x10 の箱、x=14 に半径2の貫通穴


def make_log(ops):
    return {"step": {"path": STEP, "sha256": sha256_of(STEP)}, "ops": ops}


P1 = {"id": "P1", "type": "plane", "origin": [8, 0, 0], "normal": [1, 0, 0]}
P2 = {"id": "P2", "type": "plane", "origin": [14, 0, 0], "normal": [1, 0, 0]}


def test_initial_ids_follow_topexp_order():
    m = lineage.Model.from_step(STEP)
    assert m.entities("S") == ["S1"]
    assert m.entities("F") == [f"F{i}" for i in range(1, 8)]  # 6面 + 穴の円筒面


def test_plane_partition_children_and_generated():
    m = lineage.replay(make_log([P1]))
    ids = set(m.ids)
    # 割られたソリッド・面は親の子。法線の正側 (x>8) が .1
    assert {"S1.1", "S1.2"} <= ids
    c = lineage.id_table(m)
    assert c["S1.1"][0] > 8 > c["S1.2"][0]
    # 切断面と交線・交点は操作の子
    assert "P1.F1" in ids and c["P1.F1"][0] == pytest.approx(8.0)
    assert {"P1.E1", "P1.E2", "P1.E3", "P1.E4"} <= ids
    assert {"P1.V1", "P1.V2", "P1.V3", "P1.V4"} <= ids
    # 触れていない面はそのまま
    assert "F1" in ids and "F6" in ids
    # created_by が記録されている
    assert m.created_by["S1.1"] == "P1" and m.created_by["P1.F1"] == "P1"


def test_grandchildren():
    m = lineage.replay(make_log([P1, P2]))
    assert {"S1.1.1", "S1.1.2", "S1.2"} == set(m.entities("S"))
    assert "F2.1.1" in m.ids and "F2.1.2" in m.ids and "F2.2" in m.ids
    # 穴 (半径2) を通る P2 は切断面を2枚にする
    assert {"P2.F1", "P2.F2"} <= set(m.ids)


def test_cylinder_partition_inside_is_first():
    cyl = {"id": "P3", "type": "cylinder", "origin": [14, 0, 0], "axis": [0, 0, 1], "radius": 3.5, "seam": [1, 0, 0]}
    m = lineage.replay(make_log([P2, cyl]))
    c = lineage.id_table(m)
    # P2 の切断面 P2.F1 (y<0) は円筒で内外に割れ、内側が .1
    import math
    r1 = math.hypot(c["P2.F1.1"][0] - 14, c["P2.F1.1"][1])
    r2 = math.hypot(c["P2.F1.2"][0] - 14, c["P2.F1.2"][1])
    assert r1 < 3.5 < r2


def test_replay_is_deterministic_across_processes(tmp_path):
    log = make_log([P1, P2])
    path = tmp_path / "model.json"
    path.write_text(json.dumps(log))
    runs = []
    for _ in range(2):
        out = subprocess.run([sys.executable, "-m", "keiei_yujin.cli", "replay", str(path)], check=True, capture_output=True, text=True).stdout
        runs.append(json.loads(out))
    assert runs[0] == runs[1]


def test_step_hash_is_checked():
    log = make_log([])
    log["step"]["sha256"] = "0" * 64
    with pytest.raises(RuntimeError):
        lineage.replay(log)
