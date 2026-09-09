#!/usr/bin/env python3
"""直三棱柱 DE 线面关系：Manim 边画边讲。"""

from __future__ import annotations

import numpy as np
from manim import *

CN = "Songti SC"


def cn(text: str, size: float = 28, color=WHITE) -> Text:
    return Text(text, font=CN, font_size=size, color=color)


class PrismDE(ThreeDScene):
    def construct(self):
        a, h = 2.0, 2.0
        C = np.array([0.0, 0.0, 0.0])
        A = np.array([a, 0.0, 0.0])
        B = np.array([0.0, a, 0.0])
        C1 = np.array([0.0, 0.0, h])
        A1 = A + C1
        B1 = B + C1
        D = (A + B) / 2
        E = (A + C1) / 2

        self.set_camera_orientation(phi=70 * DEGREES, theta=-48 * DEGREES, zoom=1.0)
        self.begin_ambient_camera_rotation(rate=0.035)

        title = cn("直三棱柱 ABC-A₁B₁C₁", 34, GOLD)
        title.to_corner(UL).shift(DOWN * 0.05)
        self.add_fixed_in_frame_mobjects(title)
        self.play(FadeIn(title))

        cap = cn("已知：∠ACB=90°，AC=BC，D、E 分别是 AB、AC₁ 中点", 22, GREY_A)
        cap.to_edge(DOWN, buff=0.3)
        self.add_fixed_in_frame_mobjects(cap)
        self.play(FadeIn(cap))

        def seg(p, q, dashed=False, color=WHITE, width=3.2):
            if dashed:
                return DashedLine(p, q, color=color, stroke_width=width, dash_length=0.1)
            return Line(p, q, color=color, stroke_width=width)

        vis = VGroup(
            seg(A, B),
            seg(A, A1),
            seg(B, B1),
            seg(A1, B1),
            seg(A1, C1),
            seg(B1, C1),
        )
        hid = VGroup(
            seg(A, C, True),
            seg(B, C, True),
            seg(C, C1, True),
        )
        ac1 = seg(A, C1, True, YELLOW, 2.4)
        base = Polygon(C, A, B, fill_color=BLUE_E, fill_opacity=0.2, stroke_width=0)
        side_bc = Polygon(B, C, C1, B1, fill_color=TEAL_E, fill_opacity=0.28, stroke_width=0)
        side_ac = Polygon(A, C, C1, A1, fill_color=ORANGE, fill_opacity=0.2, stroke_width=0)

        pts = [A, B, C, A1, B1, C1]
        names = ["A", "B", "C", "A₁", "B₁", "C₁"]
        nudges = [
            LEFT * 0.22 + IN * 0.05,
            RIGHT * 0.22,
            LEFT * 0.28 + DOWN * 0.12,
            LEFT * 0.22 + OUT * 0.08,
            RIGHT * 0.2 + OUT * 0.08,
            LEFT * 0.28 + OUT * 0.1,
        ]
        dots = VGroup(*[Dot3D(p, color=WHITE, radius=0.055) for p in pts])
        labs = VGroup(*[self._tag(n, p + d) for n, p, d in zip(names, pts, nudges)])

        self.play(FadeIn(base), Create(vis), Create(hid), run_time=1.5)
        self.play(FadeIn(dots), FadeIn(labs))
        self.play(Create(ac1))
        self.wait(0.4)

        d_dot = Dot3D(D, color=GOLD, radius=0.07)
        e_dot = Dot3D(E, color=GOLD, radius=0.07)
        de = Line(D, E, color=GOLD, stroke_width=5.5)
        d_lab = self._tag("D", D + DOWN * 0.22 + RIGHT * 0.1, GOLD)
        e_lab = self._tag("E", E + LEFT * 0.24, GOLD)

        self._say(cap, "D 是 AB 中点")
        self.play(FadeIn(d_dot), FadeIn(d_lab))
        self._say(cap, "E 是对角线 AC₁ 的中点，连 DE")
        self.play(FadeIn(e_dot), FadeIn(e_lab), Create(de))
        self.wait(0.5)

        self._retitle(title, "（1）证明 DE ∥ 平面 BCC₁B₁")
        self.play(FadeIn(side_bc))
        self._say(cap, "C 为原点：CA→x，CB→y，CC₁→z")

        board = self._board(
            [
                "C(0,0,0)  A(a,0,0)  B(0,a,0)  C₁(0,0,h)",
                "D = AB 中点 = (a/2, a/2, 0)",
                "E = AC₁ 中点 = (a/2, 0, h/2)",
                "向量 DE = (0, −a/2, h/2)",
                "平面 BCC₁B₁ 就是 x=0，法向量 n=(1,0,0)",
                "DE · n = 0  ⇒  DE 平行于该平面",
                "D 的 x = a/2 ≠ 0，线不在面上  ⇒  得证",
            ]
        )
        self.add_fixed_in_frame_mobjects(board)
        self.play(FadeIn(board))
        drop_d = DashedLine(D, np.array([0, D[1], D[2]]), color=TEAL, stroke_width=2)
        drop_e = DashedLine(E, np.array([0, E[1], E[2]]), color=TEAL, stroke_width=2)
        self.play(Create(drop_d), Create(drop_e))
        self._say(cap, "DE 没有 x 分量，所以整条线都跟侧面平行")
        self.wait(1.8)
        self.play(FadeOut(board), FadeOut(drop_d), FadeOut(drop_e))

        self._retitle(title, "（2）CC₁=2，DE 与平面 ACC₁A₁ 成 45°")
        self.play(FadeIn(side_ac))
        self._say(cap, "线面角：sinθ = |方向向量 · 法向量| / 模")

        board2 = self._board(
            [
                "h=2，平面 ACC₁A₁ 是 y=0，n=(0,1,0)",
                "DE=(0, −a/2, 1)，|DE|=½√(a²+4)",
                "θ=45°  ⇒  √2/2 = a / √(a²+4)",
                "两边平方：1/2 = a²/(a²+4)  ⇒  a=2",
                "DE 已平行于平面 BCC₁B₁",
                "距离等于 D 到 x=0 的距离 = a/2 = 1",
            ]
        )
        self.add_fixed_in_frame_mobjects(board2)
        self.play(FadeIn(board2))
        self.wait(2.6)

        ans = cn("结论：DE 到平面 BCC₁B₁ 的距离是 1", 30, GOLD)
        ans.to_edge(DOWN, buff=0.3)
        self.add_fixed_in_frame_mobjects(ans)
        self.play(FadeOut(cap), FadeIn(ans), Indicate(de, color=GOLD, scale_factor=1.05))
        self.wait(2.2)
        self.stop_ambient_camera_rotation()
        self.wait(0.5)

    def _tag(self, name: str, pos, color=WHITE):
        t = cn(name, 22, color)
        t.move_to(pos)
        self.add_fixed_orientation_mobjects(t)
        return t

    def _say(self, cap: Text, text: str) -> None:
        nxt = cn(text, 22, GREY_A).to_edge(DOWN, buff=0.3)
        self.add_fixed_in_frame_mobjects(nxt)
        self.play(FadeOut(cap), FadeIn(nxt), run_time=0.45)
        cap.become(nxt)

    def _retitle(self, title: Text, text: str) -> None:
        nxt = cn(text, 28, GOLD).to_corner(UL).shift(DOWN * 0.05)
        self.add_fixed_in_frame_mobjects(nxt)
        self.play(FadeOut(title), FadeIn(nxt), run_time=0.4)
        title.become(nxt)

    def _board(self, lines: list[str]) -> VGroup:
        rows = VGroup(*[cn(s, 19, WHITE) for s in lines])
        rows.arrange(DOWN, aligned_edge=LEFT, buff=0.1)
        box = RoundedRectangle(
            width=min(7.6, rows.width + 0.4),
            height=rows.height + 0.32,
            corner_radius=0.1,
            fill_color=BLACK,
            fill_opacity=0.78,
            stroke_color=GOLD_E,
            stroke_width=1.2,
        )
        rows.move_to(box)
        g = VGroup(box, rows)
        g.to_corner(UR, buff=0.18)
        return g
