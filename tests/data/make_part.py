"""検証用の STEP を作る: 20x10x10 の箱に貫通穴を一つ。"""
from build123d import Box, Cylinder, Axis, Align, export_step

part = Box(20, 10, 10, align=(Align.MIN, Align.CENTER, Align.CENTER))
hole = Cylinder(2, 20, align=(Align.CENTER, Align.CENTER, Align.CENTER)).moved(
    __import__("build123d").Location((14, 0, 0))
)
part = part - hole
export_step(part, "part.step")
print("faces", len(part.faces()), "edges", len(part.edges()))
