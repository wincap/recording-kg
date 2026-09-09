#!/usr/bin/env python3
"""图1 直三棱柱：用三维图画一步、讲一步（matplotlib + ffmpeg）。"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.animation import FFMpegWriter, FuncAnimation
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

OUT = Path(__file__).resolve().parent / "media" / "prism_de_lecture.mp4"
A, H = 2.0, 2.0

C = np.array([0.0, 0.0, 0.0])
PA = np.array([A, 0.0, 0.0])
PB = np.array([0.0, A, 0.0])
C1 = np.array([0.0, 0.0, H])
A1 = PA + C1
B1 = PB + C1
D = (PA + PB) / 2
E = (PA + C1) / 2

plt.rcParams["font.sans-serif"] = ["Songti SC", "PingFang SC", "Heiti SC", "SimSun"]
plt.rcParams["axes.unicode_minus"] = False


def quad(ax, pts, color, alpha=0.22):
    ax.add_collection3d(Poly3DCollection([pts], facecolor=color, edgecolor="none", alpha=alpha))


def draw_prism(ax, *, side_bc=False, side_ac=False, show_de=False, drops=False):
    ax.cla()
    ax.set_xlim(-0.3, 2.4)
    ax.set_ylim(-0.3, 2.4)
    ax.set_zlim(-0.1, 2.4)
    ax.set_box_aspect((1, 1, 1))
    ax.view_init(elev=18, azim=-58)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_zticks([])
    ax.set_facecolor("#101214")
    ax.xaxis.pane.fill = False
    ax.yaxis.pane.fill = False
    ax.zaxis.pane.fill = False
    for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
        axis.pane.set_edgecolor((1, 1, 1, 0.08))
        axis.line.set_color((1, 1, 1, 0.2))

    vis = [(PA, PB), (PA, A1), (PB, B1), (A1, B1), (A1, C1), (B1, C1)]
    hid = [(PA, C), (PB, C), (C, C1)]
    for p, q in vis:
        ax.plot(*zip(p, q), color="#f2efe8", lw=1.7)
    for p, q in hid:
        ax.plot(*zip(p, q), color="#8b8680", lw=1.1, ls="--")
    ax.plot(*zip(PA, C1), color="#e7c45a", lw=1.2, ls="--")
    quad(ax, [C, PA, PB], "#3d6ea8", 0.18)
    if side_bc:
        quad(ax, [PB, C, C1, B1], "#2aa39a", 0.32)
    if side_ac:
        quad(ax, [PA, C, C1, A1], "#d4892a", 0.26)

    labels = {
        "A": PA + np.array([-0.18, -0.12, 0]),
        "B": PB + np.array([0.12, 0.08, 0]),
        "C": C + np.array([-0.2, -0.18, 0]),
        "A₁": A1 + np.array([-0.15, -0.05, 0.08]),
        "B₁": B1 + np.array([0.1, 0.08, 0.08]),
        "C₁": C1 + np.array([-0.22, -0.05, 0.08]),
    }
    for name, p in [("A", PA), ("B", PB), ("C", C), ("A₁", A1), ("B₁", B1), ("C₁", C1)]:
        ax.scatter(*p, color="#fff", s=18, depthshade=False)
        ax.text(*labels[name], name, color="#f4efe6", fontsize=11)

    if show_de:
        ax.scatter(*D, color="#f0c14b", s=36, depthshade=False)
        ax.scatter(*E, color="#f0c14b", s=36, depthshade=False)
        ax.plot(*zip(D, E), color="#f0c14b", lw=2.8)
        ax.text(*(D + np.array([0.08, 0.08, -0.18])), "D", color="#f0c14b", fontsize=12)
        ax.text(*(E + np.array([-0.22, -0.05, 0.05])), "E", color="#f0c14b", fontsize=12)
    if drops:
        ax.plot([D[0], 0], [D[1], D[1]], [D[2], D[2]], color="#5fd0c8", ls="--", lw=1.2)
        ax.plot([E[0], 0], [E[1], E[1]], [E[2], E[2]], color="#5fd0c8", ls="--", lw=1.2)


STEPS = [
    ("直三棱柱 ABC-A₁B₁C₁", "已知 ∠ACB=90°，AC=BC。先把柱子立起来。", dict()),
    ("标出中点", "D 是 AB 中点，E 是 AC₁ 中点，连金线 DE。", dict(show_de=True)),
    ("（1）证 DE ∥ 平面 BCC₁B₁", "青色侧面就是平面 BCC₁B₁。建系：C 原点，CA、CB、CC₁ 为 x、y、z。", dict(show_de=True, side_bc=True)),
    ("坐标与向量", "D(a/2,a/2,0)，E(a/2,0,h/2)，DE=(0,−a/2,h/2)。", dict(show_de=True, side_bc=True)),
    ("法向量垂直", "平面 x=0 的法向量 n=(1,0,0)。DE·n=0，所以 DE 平行于面。", dict(show_de=True, side_bc=True, drops=True)),
    ("线不在面上", "D 的 x=a/2≠0，DE 不落在面上，故 DE ∥ 平面 BCC₁B₁。", dict(show_de=True, side_bc=True, drops=True)),
    ("（2）线面角 45°", "橙色是平面 ACC₁A₁（y=0）。CC₁=h=2。", dict(show_de=True, side_bc=True, side_ac=True)),
    ("求棱长 a", "sin45°=√2/2 = a/√(a²+4) ⇒ a²=4 ⇒ a=2。", dict(show_de=True, side_bc=True, side_ac=True)),
    ("求距离", "DE 已平行于青色侧面，距离等于 D 的横坐标 a/2=1。", dict(show_de=True, side_bc=True, side_ac=True, drops=True)),
]


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig = plt.figure(figsize=(12.8, 7.2), dpi=120, facecolor="#14171b")
    ax = fig.add_subplot(111, projection="3d", facecolor="#14171b")
    fig.subplots_adjust(left=0.02, right=0.98, top=0.86, bottom=0.12)
    title = fig.text(0.04, 0.93, "", color="#f0c14b", fontsize=20, fontweight="bold")
    caption = fig.text(0.04, 0.05, "", color="#d8d2c6", fontsize=14)

    frames_per = 36
    total = frames_per * len(STEPS)

    def frame(i):
        step = min(i // frames_per, len(STEPS) - 1)
        head, say, kw = STEPS[step]
        draw_prism(ax, **kw)
        title.set_text(head)
        caption.set_text(say)
        return []

    ani = FuncAnimation(fig, frame, frames=total, interval=1000 / 12, blit=False)
    writer = FFMpegWriter(fps=12, metadata={"title": "直三棱柱 DE"})
    ani.save(str(OUT), writer=writer)
    plt.close(fig)
    print(OUT)


if __name__ == "__main__":
    main()
