# 形影遊刃 — keiei-yujin

FrontISTR 向けメッシャーのコア。**文字が正本、形はその影。**

- 入力 STEP をハッシュで凍結し、分割などの操作ログを再生して **系譜ID**（`F12.2`、`P1.F1`）を決める
- 系譜IDを参照してメッシュ設定・グループを書き、Gmsh で切って FrontISTR 形式（`!EGROUP` / `!SGROUP` / `!NGROUP`）に書く
- 形影転写（VS Code 拡張）で開けば、名前と面がそのまま対応する

設計と理由は [docs/design.md](docs/design.md)、計画は [docs/plan.md](docs/plan.md)。

## 使い方

```bash
pip install -e ".[dev]"
python -m pytest

cd examples
keiei-yujin replay ogrid_hex.json            # 操作ログを再生して ID 表を出す
keiei-yujin mesh   ogrid_hex.json -o out.msh # 全ヘキサで切って FrontISTR 形式に書く
```

`examples/ogrid_hex.json` は穴あきブロックを O-grid に分割し、穴の周りにリング1層を置いて全ヘキサにする例。
`examples/embed_face_in_tets.py` は分割せずに面と稜線をテトラメッシュに埋め込む例（Gmsh の API を直接使う）。

## 状態

コアの最小縦断（STEP → 分割 → 系譜ID → Gmsh → FrontISTR msh）まで。ファイル形式は JSON の仮形式、文法は未確定。
ライセンスは未決定（Gmsh が GPL のため、方針によって Gmsh の呼び方を変える）。
