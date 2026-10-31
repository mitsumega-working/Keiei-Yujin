"""keiei-yujin CLI。コアの薄い皮。表示の都合はここに持ち込まない。

  keiei-yujin replay model.json          # 操作ログを再生し、ID表 (JSON) を出す
  keiei-yujin mesh model.json -o out.msh # メッシュを切って FrontISTR 形式で書く
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import fistr, lineage, mesh


def _load_log(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        log = json.load(f)
    # STEP のパスはログファイルからの相対で解決する
    step = log["step"]
    if not os.path.isabs(step["path"]):
        step["path"] = os.path.join(os.path.dirname(os.path.abspath(path)), step["path"])
    return log


def cmd_replay(args: argparse.Namespace) -> int:
    log = _load_log(args.log)
    m = lineage.replay(log)
    table = lineage.id_table(m)
    out = {"ids": table, "created_by": m.created_by}
    json.dump(out, sys.stdout, indent=1, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def cmd_mesh(args: argparse.Namespace) -> int:
    log = _load_log(args.log)
    m = lineage.replay(log)
    settings = mesh.MeshSettings.from_dict(log.get("mesh", {}))
    groups = fistr.Groups.from_dict(log.get("groups", {}))
    stats = mesh.generate(m, settings, groups, args.output, keep_gmsh=args.gmsh)
    json.dump(stats, sys.stdout, indent=1, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="keiei-yujin")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("replay", help="操作ログを再生して ID 表を出す")
    r.add_argument("log")
    r.set_defaults(func=cmd_replay)
    g = sub.add_parser("mesh", help="メッシュを切って FrontISTR 形式で書く")
    g.add_argument("log")
    g.add_argument("-o", "--output", required=True)
    g.add_argument("--gmsh", help="Gmsh 形式も併せて書く（目視用）")
    g.set_defaults(func=cmd_mesh)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
