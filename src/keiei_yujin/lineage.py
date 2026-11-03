"""系譜ID: 入力を凍結し、操作ログを先頭から再生して ID を決める。

規則（docs/design.md「系譜ID」）
- 読み込み直後: TopExp 順に S1.., F1.., E1.., V1..
- 分割で割られたエンティティは親の子: F12 -> F12.1, F12.2（孫以降も同様）
- 分割が新しく生んだエンティティは操作の子: P1.F1, P1.E1, P1.V1
- 兄弟の順序: 操作ごとの side() が大きい順（平面は法線の正側、円筒は内側が .1）。
  同じ側に複数あれば重心座標で安定化する
- 幾何的な特徴での照合はしない
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from OCP.BRepAlgoAPI import BRepAlgoAPI_Splitter
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.TopoDS import TopoDS_Shape
from OCP.collections import List_TopoDS_Shape as ListOfShape
from OCP.gp import gp_Ax3, gp_Cylinder, gp_Dir, gp_Pln, gp_Pnt

from .geometry import KINDS, centroid, explore, load_step, sha256_of

_BIG = 1e4


@dataclass
class Model:
    shape: TopoDS_Shape
    ids: dict[str, TopoDS_Shape] = field(default_factory=dict)  # 系譜ID -> 形状
    created_by: dict[str, str] = field(default_factory=dict)  # 系譜ID -> それを生んだ操作ID
    tools: dict[str, TopoDS_Shape] = field(default_factory=dict)  # 操作ID -> 道具（形状本体には含めない）

    # ---- 生成 -------------------------------------------------------------
    @classmethod
    def from_step(cls, path: str) -> "Model":
        shape = load_step(path)
        m = cls(shape)
        for kind in KINDS:
            for i, s in enumerate(explore(shape, kind), 1):
                m.ids[f"{kind}{i}"] = s
        return m

    def id_of(self, s: TopoDS_Shape, include_tools: bool = False) -> str | None:
        """形状から系譜IDを引く。形状本体側の ID を優先し、道具の ID は include_tools のときだけ返す。"""
        tool_ids = self.tool_ids()
        hit = None
        for k, v in self.ids.items():
            if v.IsSame(s):
                if k not in tool_ids:
                    return k
                hit = hit or k
        return hit if include_tools else None

    def entities(self, kind: str) -> list[str]:
        """種別ごとの系譜ID一覧（登録順）。道具のエンティティは含めない。"""
        return [k for k in self.ids if kind_of(k) == kind and k not in self.tool_ids()]

    def tool_ids(self) -> set[str]:
        return {k for k, op in self.created_by.items() if op in self.tools}

    # ---- 生成操作（道具） ---------------------------------------------------
    def add_tool(self, op_id: str, tool: TopoDS_Shape) -> "Model":
        """道具を登録し、その面・稜線・頂点に操作の子として ID を付ける（D1.F1, K1.E2, ...）。"""
        new = Model(self.shape, dict(self.ids), dict(self.created_by), dict(self.tools))
        new.tools[op_id] = tool
        for kind in ("S", "F", "E", "V"):
            for i, s in enumerate(explore(tool, kind), 1):
                lid = f"{op_id}.{kind}{i}"
                new.ids[lid] = s
                new.created_by[lid] = op_id
        return new

    # ---- 分割操作 -----------------------------------------------------------
    def partition_plane(self, op_id: str, origin, normal, fuzzy: float = 0.0) -> "Model":
        """平面で分割。法線の正側が .1。"""
        tool = BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(*origin), gp_Dir(*normal)), -_BIG, _BIG, -_BIG, _BIG).Face()

        def side(s: TopoDS_Shape) -> float:
            c = centroid(s)
            return sum((c[i] - origin[i]) * normal[i] for i in range(3))

        return self.partition_with(op_id, tool, side, fuzzy)

    def partition_cylinder(self, op_id: str, origin, axis, radius: float, seam=(1.0, 0.0, 0.0), fuzzy: float = 0.0) -> "Model":
        """円筒面で分割。内側が .1。seam は円筒面の縫い目の向き（既存の縫い目・分割面に揃える）。"""
        ax = gp_Ax3(gp_Pnt(*origin), gp_Dir(*axis), gp_Dir(*seam))
        tool = BRepBuilderAPI_MakeFace(gp_Cylinder(ax, radius), 0.0, 2 * math.pi, -_BIG, _BIG).Face()

        def side(s: TopoDS_Shape) -> float:
            c = centroid(s)
            d = [c[i] - origin[i] for i in range(3)]
            t = sum(d[i] * axis[i] for i in range(3))
            r = math.sqrt(sum((d[i] - t * axis[i]) ** 2 for i in range(3)))
            return radius - r

        return self.partition_with(op_id, tool, side, fuzzy)

    def partition_with(self, op_id: str, tool: TopoDS_Shape, side, fuzzy: float = 0.0) -> "Model":
        """任意の道具（面・ソリッド）で分割。貫通すれば分割、届かなければ埋め込み（内部面）になる。

        BRepAlgoAPI_Splitter の履歴 (Modified / IsDeleted) から系譜IDを付ける。
        """
        args, tools = ListOfShape(), ListOfShape()
        args.Append(self.shape)
        tools.Append(tool)
        sp = BRepAlgoAPI_Splitter()
        sp.SetArguments(args)
        sp.SetTools(tools)
        if fuzzy:
            sp.SetFuzzyValue(fuzzy)
        sp.Build()
        if not sp.IsDone():
            raise RuntimeError(f"{op_id}: split failed")
        result = sp.Shape()
        new = Model(result, created_by=dict(self.created_by), tools=dict(self.tools))

        # 1) 既存エンティティ: そのまま / 割られて子になる / 消える
        tool_ids = self.tool_ids()
        for pid, s in self.ids.items():
            if pid in tool_ids:  # 道具は分割の対象ではない。道具由来の新しい面は 2) で操作の子になる
                new.ids[pid] = s
                continue
            if sp.IsDeleted(s):
                continue
            mod = sp.Modified(s)
            if mod.IsEmpty():
                new.ids[pid] = s
                continue
            pieces = sorted((p for p in mod), key=lambda p: (-side(p), centroid(p)))
            if len(pieces) == 1:  # 割られていない（刻まれただけ）: ID を保つ
                new.ids[pid] = pieces[0]
                continue
            for k, p in enumerate(pieces, 1):
                new.ids[f"{pid}.{k}"] = p
                new.created_by[f"{pid}.{k}"] = op_id

        # 2) 分割が生んだエンティティ: 操作の子（道具の面がそのまま結果に残った場合も、結果側では操作の子として扱う）
        counters = {"F": 0, "E": 0, "V": 0}
        for kind in ("F", "E", "V"):
            for s in explore(result, kind):
                if new.id_of(s) is None:
                    counters[kind] += 1
                    new.ids[f"{op_id}.{kind}{counters[kind]}"] = s
                    new.created_by[f"{op_id}.{kind}{counters[kind]}"] = op_id
        return new


def _kind_of(lineage_id: str) -> str:
    """系譜IDの種別。F12.2 -> F, P1.E3 -> E, S1.1.2 -> S"""
    head = lineage_id.split(".")[0]
    if head[0] in KINDS:
        return head[0]
    # P1.F3 の形
    return lineage_id.split(".")[1][0]


def kind_of(lineage_id: str) -> str:
    return _kind_of(lineage_id)


# ---- 操作ログ（正本） -----------------------------------------------------

def replay(log: dict) -> Model:
    """操作ログを先頭から再生する。STEP はハッシュで固定されている。"""
    step = log["step"]
    if sha256_of(step["path"]) != step["sha256"]:
        raise RuntimeError("STEP file changed since the log was written")
    from . import ops  # 循環 import を避けるためここで

    m = Model.from_step(step["path"])
    for op in log.get("ops", []):
        m = ops.apply(m, op)
    return m


def id_table(m: Model, ndigits: int = 6) -> dict[str, list[float]]:
    """ID -> 重心。決定性の検算用（別プロセスで再生して一致を確かめる）。"""
    return {k: [round(x, ndigits) for x in centroid(v)] for k, v in m.ids.items()}
