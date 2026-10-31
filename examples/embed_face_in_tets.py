"""テトラ自動メッシュの中に、面と稜線を埋め込む（分割はしない）。Gmsh の mesh.embed を使う。"""
import gmsh

gmsh.initialize()
gmsh.option.setNumber("General.Terminal", 0)
vols = gmsh.model.occ.importShapes("part.step")  # 穴あきブロック（分割なし）
vol = [t for d, t in vols if d == 3][0]

# 埋め込む面: x=10 の正方形パッチ (y,z ∈ [-3,3])。ソリッドの内側に完全に収まる
p = [gmsh.model.occ.addPoint(10, y, z) for y, z in ((-3, -3), (3, -3), (3, 3), (-3, 3))]
l = [gmsh.model.occ.addLine(p[i], p[(i + 1) % 4]) for i in range(4)]
patch = gmsh.model.occ.addPlaneSurface([gmsh.model.occ.addCurveLoop(l)])
# 埋め込む稜線: 斜めの線分 (5,-2,-2)-(5,2,2)
q1, q2 = gmsh.model.occ.addPoint(5, -2, -2), gmsh.model.occ.addPoint(5, 2, 2)
seg = gmsh.model.occ.addLine(q1, q2)
gmsh.model.occ.synchronize()

gmsh.model.mesh.embed(2, [patch], 3, vol)
gmsh.model.mesh.embed(1, [seg], 3, vol)
gmsh.option.setNumber("Mesh.MeshSizeMax", 1.5)
gmsh.option.setNumber("Mesh.Algorithm3D", 1)  # Delaunay（埋め込みは Delaunay / HXT で有効）
gmsh.model.mesh.generate(3)

# 検証: 埋め込んだ面の三角形は、ちょうど2つのテトラの面になっているか
_, ttags, tnodes = gmsh.model.mesh.getElements(3, vol)
tets = [tuple(int(x) for x in tnodes[0][4 * i : 4 * i + 4]) for i in range(len(ttags[0]))]
from itertools import combinations
faces, edges = {}, {}
for t in tets:
    for f in combinations(sorted(t), 3):
        faces[f] = faces.get(f, 0) + 1
    for e in combinations(sorted(t), 2):
        edges[e] = edges.get(e, 0) + 1
_, ptags, pnodes = gmsh.model.mesh.getElements(2, patch)
tris = [tuple(sorted(int(x) for x in pnodes[0][3 * i : 3 * i + 3])) for i in range(len(ptags[0]))]
owners = [faces.get(f, 0) for f in tris]
_, stags, snodes = gmsh.model.mesh.getElements(1, seg)
segs = [tuple(sorted(int(x) for x in snodes[0][2 * i : 2 * i + 2])) for i in range(len(stags[0]))]
print(f"tets={len(tets)}")
print(f"embedded face: {len(tris)} triangles, tet-owners per triangle = {sorted(set(owners))} (2 が期待値)")
print(f"embedded edge: {len(segs)} segments, all are tet edges = {all(e in edges for e in segs)}")
q = gmsh.model.mesh.getElementQualities(ttags[0], "gamma")
print(f"gamma min={min(q):.3f}")
gmsh.write("embed.msh")
gmsh.finalize()
