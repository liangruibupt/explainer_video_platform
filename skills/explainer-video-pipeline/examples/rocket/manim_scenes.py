import os
from manim import *

FONT = "Noto Sans CJK SC"


def fill_to_length(scene):
    """Pad with a hold so the clip lasts exactly as long as its narration line."""
    secs = float(os.environ.get("SCENE_SECONDS", "3"))
    scene.wait(max(0.04, secs - scene.renderer.time))


class Launch(Scene):
    def construct(self):
        ground = Line(LEFT * 7, RIGHT * 7, color=GREY_D).shift(DOWN * 2.5)
        rocket = Polygon([0, 1, 0], [0.35, 0.3, 0], [0.35, -0.8, 0], [-0.35, -0.8, 0],
                         [-0.35, 0.3, 0], color=WHITE, fill_opacity=1).shift(DOWN * 1.7)
        self.add(ground, rocket)
        self.wait(0.4)
        plume = Triangle(color=BLUE_B, fill_opacity=0.8).scale(0.4).rotate(PI) \
            .next_to(rocket, DOWN, buff=0)
        self.play(FadeIn(plume, shift=DOWN * 0.3), run_time=0.6)
        group = VGroup(rocket, plume)
        up = Arrow(ORIGIN, UP * 1.5, color=GOLD).next_to(rocket, RIGHT, buff=0.6)
        label = Text("反作用力 ↑", font=FONT, font_size=32, color=GOLD).next_to(up, RIGHT)
        self.play(group.animate.shift(UP * 3), GrowArrow(up), Write(label), run_time=2.2)
        fill_to_length(self)


class Orbit(Scene):
    def construct(self):
        planet = Circle(radius=1.2, color=BLUE_E, fill_opacity=1)
        ring = Circle(radius=2.6).rotate(PI / 2)
        path = DashedVMobject(Circle(radius=2.6, color=GREY_B))
        dot = Dot(ring.point_from_proportion(0), radius=0.12, color=WHITE)
        name = Text("轨道", font=FONT, font_size=32, color=GREY_B).next_to(path, DOWN, buff=0.2)
        self.play(FadeIn(planet), Create(path), Write(name), run_time=1)
        secs = float(os.environ.get("SCENE_SECONDS", "4"))
        self.play(MoveAlongPath(dot, ring),
                  run_time=max(1, secs - 1.3), rate_func=linear)
        fill_to_length(self)
