# Abaqus/CAE のパーティション機能と、形影遊刃での対応案

Abaqus/CAE 2016 User's Guide 70.4 の一覧を基準に、現状の道具（OCC `BRepAlgoAPI_Splitter` による `partition_with`、build123d のスケッチ／押し出し／スイープ、OCC の各 API）で何がどこまで覆えるかを整理する。

凡例: ◎ 今の仕組みで直接できる ／ ○ 道具を作る生成操作を足せばできる ／ △ 近似か制約付き ／ × 当面やらない

## セル（ソリッド）の分割

| A/CAE の方法 | 内容 | 対応 | 道具の作り方 |
|---|---|---|---|
| Define cutting plane | 点＋法線、3点、辺＋点で平面を定め、セルを貫通して切る | ◎ | `partition_plane`（済）。3点・辺＋点は平面を求める前処理だけ |
| Use datum plane | 基準面の延長で切る | ◎ | 同上。基準面は「生成」操作の一種として ID を持たせる |
| Extend face | 既存の面（平面・円筒・円錐・球）を延長して切る | ○ | `BRep_Tool.Surface` で下地曲面を取り、`BRepBuilderAPI_MakeFace(surface, 範囲)` で大きな面を作って Splitter にかける。円筒は `partition_cylinder`（済）と同じ。縫い目の向きは既存面から引き継ぐ |
| Extrude/Sweep edges | 既存の辺（同一平面上・連結）を直線方向か辺に沿って掃引し、その面で切る | ○ | build123d `extrude` / `sweep`（パスは辺の ID 参照）で面を作る。A/CAE の制約（辺が同一平面、パス接線が平面に垂直）は OCC では不要だが、結果が自己交差しないかの検査は要る |
| Use n-sided patch | 辺のループから曲面パッチを張って切る | ○ | `BRepOffsetAPI_MakeFilling`（または `BRepFill_Filling`）でループを埋める面を作り Splitter へ。ループが平面上なら `MakeFace(wire, True)` で足りる |
| Sketch planar partition | 基準面上のスケッチが交わる所で切る | ○ | 文字スケッチ → 面（必要なら押し出して板にする）→ Splitter。スケッチの「生成」操作が前提 |

A/CAE の「平面はセルを貫通しなければならない」という制約は、形影遊刃では不要。道具が届かない部分は内部面として埋め込まれる（検証済み）。これは A/CAE より自由度が高い。

## 面の分割

| A/CAE の方法 | 内容 | 対応 | 道具の作り方 |
|---|---|---|---|
| Sketch planar partition | 面上または基準面上のスケッチを面へ投影して切る | ○ | スケッチを面の法線方向（または指定方向）に押し出して板にし、Splitter にかける。曲面への投影は押し出し板との交線になるので、最初は平面投影に限定 |
| Shortest path between 2 points | 2点間の測地線（曲面上の最短路）で切る | △ | OCC に測地線はない。平面上なら直線で ◎。曲面は「2点と面の法線を含む平面」との交線で近似（円筒の周方向なら一致）。真の測地線は TO-BE |
| Use datum plane | 基準面との交線で切る | ◎ | `partition_plane` は面も一緒に割る（済） |
| Curved path normal to 2 edges | 2辺に直交するベジエ曲線で切る | ○ | 曲面のパラメータ空間で 2D ベジエを作り `BRepBuilderAPI_MakeEdge(curve2d, surface)`、その辺を含む法線方向の板で切る。優先度は低い |
| Extend another face | 別の面の延長との交線で切る | ○ | セルの Extend face と同じ道具。面だけ割りたいときも Splitter は同じ（ソリッドの内部面が増えるだけ） |
| Intersect by other faces | 他の面との交線で切る | ◎ | その面（またはソリッド）を道具に `partition_with` |
| Project edges | 辺を面へ垂直投影して切る | ○ | `BRepProj_Projection`（方向投影）で面上の辺を作り、その辺を法線方向に押し出した板で切る。端の延長は A/CAE と同じく任意オプション |
| Auto-partition | 自由四角形メッシュ前に 3〜5 辺の領域へ自動分割 | × | Gmsh 側の四角形化に任せる。構造格子の自動ブロック分割は別問題（design.md） |

## 辺の分割

| A/CAE の方法 | 内容 | 対応 | 道具の作り方 |
|---|---|---|---|
| Specify parameter by location | 辺上の点を選んで割る | ◎ | 頂点を道具に `partition_with`（Splitter は頂点も受ける）。位置はパラメータとして記録 |
| Enter parameter | 0〜1 のパラメータで割る | ◎ | 同上。文字で書く形影遊刃ではこれが基本形 |
| Select midpoint/datum point | 中点や基準点で割る | ◎ | 同上（中点＝パラメータ 0.5 の糖衣） |
| Use datum plane | 平面と交わる所で割る（複数辺の揃え） | ◎ | `partition_plane`（済）。面やソリッドも一緒に割れるのが A/CAE との違いで、辺だけ割りたい場合は道具を辺の近傍に限定する必要がある（要検討） |

## 形影遊刃側で足すべき「生成」操作（優先順）

1. **基準面**（点＋法線、3点、辺＋点、既存面のオフセット）— セル・面・辺の分割の大半がこれで済む
2. **面の延長**（既存面の下地曲面から大きな面を作る）— 穴まわりの円筒延長、フィレット付き形状の分割に効く
3. **文字スケッチ → 面 → 押し出し板**（線・円弧・円・スプライン、ID 参照の座標）
4. **辺の掃引**（直線方向／辺に沿う）
5. **n 辺パッチ**（`MakeFilling`）
6. **辺の投影**（`BRepProj`）

## 注意点
- A/CAE は「セル／面／辺」で分割の対象を限定するが、Splitter は道具が触れたもの全部を割る。「辺だけ割りたい」「この面だけ割りたい」は、道具の範囲を絞る（面の範囲、押し出し長さ）か、対象ソリッドだけを引数にするかで制御する。後者は界面の整合を崩すので、前者を基本にする
- 道具が既存の縫い目や面境界と重なると、微小面・微小辺が出ることがある。各操作後の形状検査（design.md）で拾う
- 測地線（Shortest path）だけは OCC に無いので、近似で始めて要望があれば自前実装

出典: Abaqus/CAE User's Guide 2016, §70.4.1–70.4.3（methods for partitioning edges / faces / cells）
