#!/usr/bin/env python3
"""Create a procedural cinematic chase video without source images.

Every frame is drawn as vector SVG, rasterized in memory, and muxed into a
Motion-JPEG AVI with a generated PCM soundtrack. The result is self-contained
and does not depend on stock footage or generated still images.
"""

from __future__ import annotations

import argparse
import math
import random
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET
from array import array
from pathlib import Path

WIDTH = 960
HEIGHT = 540
FPS = 24
DURATION = 12
FRAMES = FPS * DURATION
SAMPLE_RATE = 44_100
GROUND_Y = 423


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def smooth(v: float) -> float:
    v = clamp(v)
    return v * v * (3.0 - 2.0 * v)


def lerp(a: float, b: float, v: float) -> float:
    return a + (b - a) * v


def chunk(fourcc: bytes, payload: bytes) -> bytes:
    pad = b"\0" if len(payload) & 1 else b""
    return fourcc + struct.pack("<I", len(payload)) + payload + pad


def list_chunk(kind: bytes, payload: bytes) -> bytes:
    return chunk(b"LIST", kind + payload)


def tree_svg(x: float, y: float, scale: float, color: str, opacity: float) -> str:
    trunk_w = 5 * scale
    h = 115 * scale
    crown = (
        f'<polygon points="{x:.1f},{y-h:.1f} {x-35*scale:.1f},{y-27*scale:.1f} '
        f'{x+35*scale:.1f},{y-27*scale:.1f}"/>'
        f'<polygon points="{x:.1f},{y-h*0.80:.1f} {x-29*scale:.1f},{y-10*scale:.1f} '
        f'{x+29*scale:.1f},{y-10*scale:.1f}"/>'
        f'<polygon points="{x:.1f},{y-h*0.58:.1f} {x-23*scale:.1f},{y:.1f} '
        f'{x+23*scale:.1f},{y:.1f}"/>'
    )
    return (
        f'<g fill="{color}" opacity="{opacity:.3f}">'
        f'<rect x="{x-trunk_w/2:.1f}" y="{y-h*.45:.1f}" width="{trunk_w:.1f}" height="{h*.45:.1f}"/>'
        f'{crown}</g>'
    )


def forest_svg(frame: int, camera: float) -> str:
    parts: list[str] = []
    # Seeded layers remain spatially coherent while camera parallax adds depth.
    for layer, (count, base_y, parallax, lo_s, hi_s, color, opacity) in enumerate(
        [
            (28, 337, 0.16, 0.72, 1.10, "#708399", 0.32),
            (24, 385, 0.31, 0.90, 1.45, "#3b5268", 0.55),
            (20, 432, 0.55, 1.25, 2.00, "#172d3d", 0.84),
        ]
    ):
        rng = random.Random(7110 + layer)
        span = 2200
        trees = []
        for _ in range(count):
            world_x = rng.uniform(-350, span)
            scale = rng.uniform(lo_s, hi_s)
            trees.append((world_x, scale))
        for world_x, scale in trees:
            x = world_x - camera * parallax
            if -180 < x < WIDTH + 180:
                parts.append(tree_svg(x, base_y, scale, color, opacity))
    return "".join(parts)


def snow_svg(frame: int, intensity: float = 1.0) -> str:
    rng = random.Random(1984)
    parts: list[str] = []
    count = int(115 * intensity)
    for i in range(count):
        x0 = rng.uniform(0, WIDTH)
        y0 = rng.uniform(0, HEIGHT)
        speed = rng.uniform(2.0, 7.2)
        wind = rng.uniform(1.2, 3.8)
        x = (x0 - frame * wind + math.sin(frame * 0.04 + i) * 7) % (WIDTH + 80) - 40
        y = (y0 + frame * speed) % (HEIGHT + 70) - 35
        r = rng.uniform(0.8, 3.0)
        alpha = rng.uniform(0.25, 0.88)
        parts.append(
            f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{x+wind*2.4:.1f}" '
            f'y2="{y-speed*1.7:.1f}" stroke="#f5fbff" stroke-width="{r:.1f}" '
            f'opacity="{alpha:.2f}" stroke-linecap="round"/>'
        )
    return "".join(parts)


def powder_svg(x: float, y: float, frame: int, amount: float) -> str:
    rng = random.Random(frame * 43 + int(x))
    parts = []
    for _ in range(int(22 * amount)):
        dx = rng.uniform(-58, 35)
        dy = rng.uniform(-20, 7)
        rx = rng.uniform(2, 11)
        parts.append(
            f'<ellipse cx="{x+dx:.1f}" cy="{y+dy:.1f}" rx="{rx:.1f}" ry="{rx*.38:.1f}" '
            f'fill="#eaf5fc" opacity="{rng.uniform(.18,.72):.2f}"/>'
        )
    return "".join(parts)


def human_run_svg(x: float, ground: float, phase: float, axe_visible: bool = True) -> str:
    s = math.sin(phase)
    c = math.cos(phase)
    bob = abs(s) * 4
    hip_y = ground - 61 - bob
    shoulder_x, shoulder_y = x + 15, ground - 124 - bob
    head_x, head_y = x + 20, ground - 151 - bob

    knee_back = (x - 12 + 25 * s, hip_y + 34)
    foot_back = (x - 26 + 49 * s, ground - 3)
    knee_front = (x + 8 - 29 * s, hip_y + 31)
    foot_front = (x + 33 - 52 * s, ground - 2)

    elbow_back = (x + 2 - 23 * s, shoulder_y + 31)
    hand_back = (x - 8 - 38 * s, shoulder_y + 54)
    elbow_front = (x + 29 + 24 * s, shoulder_y + 28)
    hand_front = (x + 41 + 35 * s, shoulder_y + 48)

    axe = ""
    if axe_visible:
        axe = (
            f'<line x1="{hand_back[0]:.1f}" y1="{hand_back[1]:.1f}" '
            f'x2="{hand_back[0]-31:.1f}" y2="{hand_back[1]+29:.1f}" stroke="#6e4329" stroke-width="6"/>'
            f'<path d="M {hand_back[0]-39:.1f},{hand_back[1]+20:.1f} l 19,8 l -8,14 l -21,-8 z" '
            f'fill="url(#steel)" stroke="#d7e4e9" stroke-width="1"/>'
        )

    return f'''
    <g filter="url(#softShadow)">
      <ellipse cx="{x:.1f}" cy="{ground+4:.1f}" rx="57" ry="10" fill="#092033" opacity=".32"/>
      <path d="M {x:.1f},{hip_y:.1f} Q {knee_back[0]:.1f},{knee_back[1]:.1f} {foot_back[0]:.1f},{foot_back[1]:.1f}"
            fill="none" stroke="#101923" stroke-width="17" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M {shoulder_x:.1f},{shoulder_y:.1f} Q {elbow_back[0]:.1f},{elbow_back[1]:.1f} {hand_back[0]:.1f},{hand_back[1]:.1f}"
            fill="none" stroke="#263c39" stroke-width="15" stroke-linecap="round"/>
      {axe}
      <path d="M {x-11:.1f},{hip_y+5:.1f} Q {x-18:.1f},{ground-100-bob:.1f} {shoulder_x-10:.1f},{shoulder_y-8:.1f}
               Q {shoulder_x+21:.1f},{shoulder_y+4:.1f} {x+17:.1f},{hip_y+8:.1f} Z"
            fill="url(#parka)" stroke="#6e8276" stroke-width="2"/>
      <path d="M {x:.1f},{hip_y:.1f} Q {knee_front[0]:.1f},{knee_front[1]:.1f} {foot_front[0]:.1f},{foot_front[1]:.1f}"
            fill="none" stroke="#172430" stroke-width="18" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M {shoulder_x+2:.1f},{shoulder_y:.1f} Q {elbow_front[0]:.1f},{elbow_front[1]:.1f} {hand_front[0]:.1f},{hand_front[1]:.1f}"
            fill="none" stroke="#3c554c" stroke-width="16" stroke-linecap="round"/>
      <circle cx="{head_x:.1f}" cy="{head_y:.1f}" r="18" fill="#b78067" stroke="#17242d" stroke-width="6"/>
      <path d="M {head_x-16:.1f},{head_y-3:.1f} Q {head_x+2:.1f},{head_y+18:.1f} {head_x+17:.1f},{head_y+3:.1f}"
            fill="none" stroke="#263036" stroke-width="6"/>
      <path d="M {shoulder_x-11:.1f},{shoulder_y-5:.1f} Q {x-16:.1f},{shoulder_y+6:.1f} {x-45-20*c:.1f},{shoulder_y+2:.1f}"
            fill="none" stroke="#a6222d" stroke-width="10" stroke-linecap="round"/>
    </g>'''


def human_action_svg(x: float, ground: float, stage: str, p: float) -> str:
    """Draw bitten, recovering, striking, and aftermath poses."""
    p = clamp(p)
    if stage == "bitten":
        # Torso is dragged backward as the bear grabs the padded shoulder.
        lean = lerp(10, -18, smooth(p))
        yoff = lerp(0, 31, smooth(p))
        hip = (x, ground - 57 + yoff)
        shoulder = (x + lean, ground - 121 + yoff)
        head = (shoulder[0] + 9, shoulder[1] - 29)
        hand = (x + 54, ground - 72 + yoff)
        return f'''
        <g filter="url(#softShadow)">
          <ellipse cx="{x:.1f}" cy="{ground+5:.1f}" rx="66" ry="11" fill="#092033" opacity=".38"/>
          <path d="M {hip[0]-3:.1f},{hip[1]:.1f} Q {x-28:.1f},{ground-27:.1f} {x-58:.1f},{ground-2:.1f}" fill="none" stroke="#111d29" stroke-width="18" stroke-linecap="round"/>
          <path d="M {hip[0]+4:.1f},{hip[1]:.1f} Q {x+27:.1f},{ground-20:.1f} {x+52:.1f},{ground-2:.1f}" fill="none" stroke="#172734" stroke-width="18" stroke-linecap="round"/>
          <path d="M {hip[0]-13:.1f},{hip[1]+3:.1f} L {shoulder[0]-16:.1f},{shoulder[1]-3:.1f} Q {shoulder[0]+13:.1f},{shoulder[1]-13:.1f} {hip[0]+17:.1f},{hip[1]+4:.1f} Z" fill="url(#parka)" stroke="#7f9187" stroke-width="2"/>
          <path d="M {shoulder[0]+4:.1f},{shoulder[1]+5:.1f} Q {x+25:.1f},{ground-91+yoff:.1f} {hand[0]:.1f},{hand[1]:.1f}" fill="none" stroke="#405b51" stroke-width="16" stroke-linecap="round"/>
          <circle cx="{head[0]:.1f}" cy="{head[1]:.1f}" r="18" fill="#b78067" stroke="#17242d" stroke-width="6"/>
          <path d="M {shoulder[0]-9:.1f},{shoulder[1]:.1f} Q {x+1:.1f},{shoulder[1]+8:.1f} {x+43:.1f},{shoulder[1]+10:.1f}" fill="none" stroke="#a6222d" stroke-width="10"/>
          <line x1="{x-4:.1f}" y1="{ground-54+yoff:.1f}" x2="{x+28:.1f}" y2="{ground-19+yoff:.1f}" stroke="#6e4329" stroke-width="7"/>
          <path d="M {x+20:.1f},{ground-29+yoff:.1f} l 21,6 l -8,16 l -21,-9 z" fill="url(#steel)"/>
        </g>'''

    if stage == "recover":
        rise = smooth(p)
        hip_y = lerp(ground - 34, ground - 61, rise)
        shoulder_y = lerp(ground - 92, ground - 132, rise)
        return f'''
        <g filter="url(#softShadow)">
          <ellipse cx="{x:.1f}" cy="{ground+5:.1f}" rx="68" ry="11" fill="#092033" opacity=".36"/>
          <path d="M {x:.1f},{hip_y:.1f} Q {x+24:.1f},{ground-18:.1f} {x+45:.1f},{ground-2:.1f}" fill="none" stroke="#172430" stroke-width="18" stroke-linecap="round"/>
          <path d="M {x-2:.1f},{hip_y:.1f} Q {x-31:.1f},{ground-15:.1f} {x-60:.1f},{ground-2:.1f}" fill="none" stroke="#111d29" stroke-width="18" stroke-linecap="round"/>
          <path d="M {x-13:.1f},{hip_y+5:.1f} L {x-13:.1f},{shoulder_y:.1f} Q {x+13:.1f},{shoulder_y-9:.1f} {x+18:.1f},{hip_y+4:.1f} Z" fill="url(#parka)" stroke="#7f9187" stroke-width="2"/>
          <circle cx="{x-2:.1f}" cy="{shoulder_y-27:.1f}" r="18" fill="#b78067" stroke="#17242d" stroke-width="6"/>
          <path d="M {x-12:.1f},{shoulder_y+3:.1f} Q {x-32:.1f},{shoulder_y+24:.1f} {x-18:.1f},{shoulder_y+45:.1f}" fill="none" stroke="#385148" stroke-width="16" stroke-linecap="round"/>
          <path d="M {x+7:.1f},{shoulder_y+2:.1f} Q {x+29:.1f},{shoulder_y+24:.1f} {x+12:.1f},{shoulder_y+49:.1f}" fill="none" stroke="#405b51" stroke-width="16" stroke-linecap="round"/>
          <line x1="{x-16:.1f}" y1="{shoulder_y+45:.1f}" x2="{x-83+38*rise:.1f}" y2="{shoulder_y-5-78*rise:.1f}" stroke="#6e4329" stroke-width="7" stroke-linecap="round"/>
          <path d="M {x-95+38*rise:.1f},{shoulder_y-15-78*rise:.1f} l 24,5 l -2,20 l -25,-2 z" fill="url(#steel)" stroke="#d7e4e9"/>
          <path d="M {x-12:.1f},{shoulder_y-1:.1f} Q {x+10:.1f},{shoulder_y+8:.1f} {x+48:.1f},{shoulder_y+5:.1f}" fill="none" stroke="#a6222d" stroke-width="10"/>
        </g>'''

    if stage == "strike":
        # Man faces left. The axe travels through a broad readable arc.
        swing = smooth(p)
        hip_y = ground - 62
        shoulder_y = ground - 132
        pivot_x, pivot_y = x - 10, shoulder_y + 13
        # The axe starts above the man's right shoulder and finishes against
        # the bear to his left; negative angles account for SVG's downwards Y.
        angle = math.radians(lerp(-62, -178, swing))
        hand_x = pivot_x + 34 * math.cos(angle)
        hand_y = pivot_y + 34 * math.sin(angle)
        axe_x = pivot_x + 108 * math.cos(angle)
        axe_y = pivot_y + 108 * math.sin(angle)
        blade_angle = angle + math.pi / 2
        bx = 18 * math.cos(blade_angle)
        by = 18 * math.sin(blade_angle)
        trail = ""
        if 0.12 < p < 0.82:
            trail = f'<path d="M {x-117:.1f},{shoulder_y-69:.1f} Q {x-152:.1f},{shoulder_y+10:.1f} {x-80:.1f},{shoulder_y+92:.1f}" fill="none" stroke="#d9f0fa" stroke-width="8" opacity=".22"/>'
        return f'''
        <g filter="url(#softShadow)">
          {trail}
          <ellipse cx="{x:.1f}" cy="{ground+5:.1f}" rx="65" ry="11" fill="#092033" opacity=".38"/>
          <path d="M {x:.1f},{hip_y:.1f} Q {x-25:.1f},{ground-29:.1f} {x-54:.1f},{ground-2:.1f}" fill="none" stroke="#172430" stroke-width="18" stroke-linecap="round"/>
          <path d="M {x+4:.1f},{hip_y:.1f} Q {x+28:.1f},{ground-30:.1f} {x+53:.1f},{ground-2:.1f}" fill="none" stroke="#111d29" stroke-width="18" stroke-linecap="round"/>
          <path d="M {x-14:.1f},{hip_y+5:.1f} L {x-18:.1f},{shoulder_y:.1f} Q {x+8:.1f},{shoulder_y-10:.1f} {x+19:.1f},{hip_y+4:.1f} Z" fill="url(#parka)" stroke="#7f9187" stroke-width="2"/>
          <circle cx="{x-18:.1f}" cy="{shoulder_y-27:.1f}" r="18" fill="#b78067" stroke="#17242d" stroke-width="6"/>
          <path d="M {x-29:.1f},{shoulder_y-28:.1f} q -12,15 -1,27" fill="none" stroke="#263036" stroke-width="6"/>
          <path d="M {x-10:.1f},{shoulder_y+7:.1f} Q {pivot_x-16:.1f},{pivot_y+12:.1f} {hand_x:.1f},{hand_y:.1f}" fill="none" stroke="#385148" stroke-width="16" stroke-linecap="round"/>
          <path d="M {x+2:.1f},{shoulder_y+8:.1f} Q {pivot_x+8:.1f},{pivot_y+20:.1f} {hand_x+6:.1f},{hand_y+5:.1f}" fill="none" stroke="#405b51" stroke-width="15" stroke-linecap="round"/>
          <line x1="{hand_x:.1f}" y1="{hand_y:.1f}" x2="{axe_x:.1f}" y2="{axe_y:.1f}" stroke="#6e4329" stroke-width="8" stroke-linecap="round"/>
          <path d="M {axe_x-bx:.1f},{axe_y-by:.1f} L {axe_x+bx:.1f},{axe_y+by:.1f} l {-14*math.cos(angle):.1f},{-14*math.sin(angle):.1f} L {axe_x-bx*1.25:.1f},{axe_y-by*1.25:.1f} Z" fill="url(#steel)" stroke="#e4f0f4" stroke-width="1.5"/>
          <path d="M {x-15:.1f},{shoulder_y:.1f} Q {x+6:.1f},{shoulder_y+7:.1f} {x+46:.1f},{shoulder_y+4:.1f}" fill="none" stroke="#a6222d" stroke-width="10"/>
        </g>'''

    # Aftermath: defensive low stance, axe still raised, bear withdrawing.
    return f'''
    <g filter="url(#softShadow)">
      <ellipse cx="{x:.1f}" cy="{ground+5:.1f}" rx="66" ry="11" fill="#092033" opacity=".38"/>
      <path d="M {x:.1f},{ground-61:.1f} Q {x-22:.1f},{ground-27:.1f} {x-55:.1f},{ground-2:.1f}" fill="none" stroke="#172430" stroke-width="18" stroke-linecap="round"/>
      <path d="M {x+4:.1f},{ground-61:.1f} Q {x+28:.1f},{ground-27:.1f} {x+55:.1f},{ground-2:.1f}" fill="none" stroke="#111d29" stroke-width="18" stroke-linecap="round"/>
      <path d="M {x-14:.1f},{ground-56:.1f} L {x-16:.1f},{ground-130:.1f} Q {x+10:.1f},{ground-139:.1f} {x+19:.1f},{ground-57:.1f} Z" fill="url(#parka)"/>
      <circle cx="{x-17:.1f}" cy="{ground-157:.1f}" r="18" fill="#b78067" stroke="#17242d" stroke-width="6"/>
      <path d="M {x-10:.1f},{ground-121:.1f} Q {x-31:.1f},{ground-94:.1f} {x-58:.1f},{ground-116:.1f}" fill="none" stroke="#405b51" stroke-width="16" stroke-linecap="round"/>
      <line x1="{x-57:.1f}" y1="{ground-115:.1f}" x2="{x-105:.1f}" y2="{ground-178:.1f}" stroke="#6e4329" stroke-width="8"/>
      <path d="M {x-119:.1f},{ground-189:.1f} l 26,4 l -1,20 l -28,-2 z" fill="url(#steel)"/>
      <path d="M {x-14:.1f},{ground-128:.1f} Q {x+8:.1f},{ground-120:.1f} {x+46:.1f},{ground-123:.1f}" fill="none" stroke="#a6222d" stroke-width="10"/>
    </g>'''


def bear_svg(x: float, ground: float, phase: float, mode: str, p: float = 0.0) -> str:
    run = math.sin(phase)
    bob = abs(run) * 5
    rotation = 0.0
    scale_x = 1.0
    jaw = 0.0
    shift_y = 0.0
    if mode == "lunge":
        rotation = lerp(0, -7, smooth(p))
        shift_y = -22 * math.sin(math.pi * clamp(p))
        jaw = smooth(p)
    elif mode == "bite":
        rotation = -5 + math.sin(p * math.pi * 4) * 2
        jaw = 1.0
        shift_y = -13
    elif mode == "recoil":
        rotation = lerp(-4, -17, smooth(p))
        scale_x = 1.0 - 0.05 * smooth(p)
        shift_y = -12 * math.sin(math.pi * clamp(p))
        jaw = 0.7
    elif mode == "retreat":
        rotation = -8
        jaw = 0.5

    body_y = ground - 76 - bob + shift_y
    # Four articulated legs create a readable gallop.
    leg1 = 27 * run
    leg2 = -27 * run
    mouth_open = 7 + jaw * 14
    eye_x = 72
    return f'''
    <g transform="translate({x:.1f} 0) rotate({rotation:.2f} 0 {body_y:.1f}) scale({scale_x:.3f} 1)" filter="url(#bearShadow)">
      <ellipse cx="0" cy="{ground+6:.1f}" rx="104" ry="13" fill="#061725" opacity=".40"/>
      <path d="M {-48+leg2:.1f},{body_y+31:.1f} Q {-62+leg2:.1f},{ground-29:.1f} {-77+leg2:.1f},{ground-3:.1f}" fill="none" stroke="#221c1b" stroke-width="25" stroke-linecap="round"/>
      <path d="M {42+leg1:.1f},{body_y+27:.1f} Q {52+leg1:.1f},{ground-28:.1f} {68+leg1:.1f},{ground-3:.1f}" fill="none" stroke="#2b2220" stroke-width="26" stroke-linecap="round"/>
      <ellipse cx="-8" cy="{body_y:.1f}" rx="91" ry="55" fill="url(#fur)" stroke="#514039" stroke-width="3"/>
      <ellipse cx="42" cy="{body_y-17:.1f}" rx="56" ry="52" fill="#382c29"/>
      <path d="M {-26+leg1:.1f},{body_y+33:.1f} Q {-17+leg1:.1f},{ground-31:.1f} {-1+leg1:.1f},{ground-3:.1f}" fill="none" stroke="#392c29" stroke-width="27" stroke-linecap="round"/>
      <path d="M {51+leg2:.1f},{body_y+27:.1f} Q {66+leg2:.1f},{ground-28:.1f} {82+leg2:.1f},{ground-3:.1f}" fill="none" stroke="#43332f" stroke-width="27" stroke-linecap="round"/>
      <circle cx="52" cy="{body_y-55:.1f}" r="15" fill="#2d2422"/>
      <circle cx="87" cy="{body_y-48:.1f}" r="14" fill="#2d2422"/>
      <ellipse cx="71" cy="{body_y-29:.1f}" rx="42" ry="36" fill="#44332e"/>
      <path d="M 69,{body_y-14:.1f} Q 99,{body_y-17-mouth_open:.1f} 113,{body_y-5-mouth_open:.1f} Q 96,{body_y+5+mouth_open:.1f} 70,{body_y+4:.1f} Z" fill="#160f11" stroke="#57433c" stroke-width="3"/>
      <path d="M 84,{body_y-14:.1f} l 5,12 l 6,-13 M 98,{body_y-12:.1f} l 4,10 l 6,-8" fill="none" stroke="#eef0dc" stroke-width="4"/>
      <ellipse cx="108" cy="{body_y-12-mouth_open*.35:.1f}" rx="13" ry="9" fill="#171315"/>
      <circle cx="{eye_x}" cy="{body_y-38:.1f}" r="4.2" fill="#ffb43b"/>
      <circle cx="{eye_x+1}" cy="{body_y-39:.1f}" r="1.5" fill="#fff4c8"/>
      <path d="M 38,{body_y-42:.1f} Q 52,{body_y-50:.1f} 65,{body_y-45:.1f}" fill="none" stroke="#231b1a" stroke-width="6"/>
    </g>'''


def frame_svg(frame: int) -> str:
    t = frame / FPS
    camera = min(t, 6.5) * 58
    shake_x = 0.0
    shake_y = 0.0
    if 5.05 < t < 6.35:
        amp = 7 * (1 - (t - 5.05) / 1.3)
        shake_x += math.sin(frame * 2.7) * amp
        shake_y += math.cos(frame * 2.2) * amp * 0.55
    if 9.2 < t < 9.55:
        amp = 10 * (1 - (t - 9.2) / 0.35)
        shake_x += math.sin(frame * 3.1) * amp
        shake_y += math.cos(frame * 2.6) * amp * 0.6

    man_x = 570.0
    bear_x = 285.0
    phase = t * math.pi * 3.9
    mode = "run"
    actor_svg = ""

    if t < 4.4:
        catch = smooth((t - 1.0) / 3.4)
        bear_x = lerp(285, 430, catch)
        actor_svg = bear_svg(bear_x, GROUND_Y, phase + 1.4, "run")
        actor_svg += human_run_svg(man_x, GROUND_Y, phase)
    elif t < 5.15:
        p = (t - 4.4) / 0.75
        bear_x = lerp(430, 475, smooth(p))
        actor_svg = bear_svg(bear_x, GROUND_Y, phase, "lunge", p)
        actor_svg += human_run_svg(man_x + 8 * p, GROUND_Y, phase)
    elif t < 6.45:
        p = (t - 5.15) / 1.30
        bear_x = 474 + math.sin(p * math.pi) * 5
        actor_svg = human_action_svg(man_x + 13, GROUND_Y, "bitten", p)
        actor_svg += bear_svg(bear_x, GROUND_Y, phase, "bite", p)
    elif t < 7.75:
        p = (t - 6.45) / 1.30
        bear_x = lerp(472, 440, smooth(p))
        actor_svg = bear_svg(bear_x, GROUND_Y, phase, "recoil", p * 0.35)
        actor_svg += human_action_svg(man_x + 15, GROUND_Y, "recover", p)
    elif t < 9.45:
        p = (t - 7.75) / 1.70
        bear_x = lerp(440, 423, smooth(p))
        actor_svg = bear_svg(bear_x, GROUND_Y, phase, "lunge", 0.22)
        actor_svg += human_action_svg(man_x + 18, GROUND_Y, "strike", p)
    elif t < 10.65:
        p = (t - 9.45) / 1.20
        bear_x = lerp(423, 315, smooth(p))
        actor_svg = bear_svg(bear_x, GROUND_Y, phase, "recoil", p)
        actor_svg += human_action_svg(man_x + 18, GROUND_Y, "strike", 1.0)
    else:
        p = (t - 10.65) / 1.35
        bear_x = lerp(315, 180, smooth(p))
        actor_svg = bear_svg(bear_x, GROUND_Y, phase, "retreat", p)
        actor_svg += human_action_svg(man_x + 18, GROUND_Y, "aftermath", p)

    impact_flash = 0.0
    if 9.36 <= t <= 9.52:
        impact_flash = (1 - abs(t - 9.44) / 0.08) * 0.42
    cold_fade = clamp((t - 11.2) / 0.8) * 0.28

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">
    <defs>
      <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#071729"/><stop offset=".46" stop-color="#32536d"/><stop offset="1" stop-color="#b3cad7"/></linearGradient>
      <linearGradient id="snowGround" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#dbeaf2"/><stop offset="1" stop-color="#7892a6"/></linearGradient>
      <linearGradient id="parka" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#4f6b5e"/><stop offset="1" stop-color="#1d3431"/></linearGradient>
      <linearGradient id="fur" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#5a453d"/><stop offset=".5" stop-color="#2c2220"/><stop offset="1" stop-color="#171416"/></linearGradient>
      <linearGradient id="steel" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#eef7fa"/><stop offset=".46" stop-color="#8fa8b0"/><stop offset="1" stop-color="#e4edf0"/></linearGradient>
      <radialGradient id="vignette"><stop offset="52%" stop-color="#000" stop-opacity="0"/><stop offset="100%" stop-color="#020711" stop-opacity=".82"/></radialGradient>
      <filter id="softShadow"><feGaussianBlur in="SourceAlpha" stdDeviation="1.1" result="b"/><feOffset dx="1" dy="3"/><feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter>
      <filter id="bearShadow"><feGaussianBlur in="SourceAlpha" stdDeviation="1.5" result="b"/><feOffset dx="2" dy="4"/><feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter>
    </defs>
    <rect width="{WIDTH}" height="{HEIGHT}" fill="url(#sky)"/>
    <circle cx="760" cy="108" r="67" fill="#d8eef7" opacity=".13"/>
    <g transform="translate({shake_x:.2f} {shake_y:.2f})">
      {forest_svg(frame, camera)}
      <path d="M -30,390 Q 180,365 370,394 T 1010,379 L 1010,560 L -30,560 Z" fill="url(#snowGround)"/>
      <path d="M -40,432 Q 145,403 310,435 T 665,427 T 1010,436" fill="none" stroke="#f1f8fb" stroke-width="14" opacity=".65"/>
      {powder_svg(man_x, GROUND_Y, frame, 0.8 if t < 5 else 0.35)}
      {powder_svg(bear_x, GROUND_Y, frame + 500, 1.0 if t < 5.2 else 0.45)}
      {actor_svg}
      {snow_svg(frame, 1.0)}
    </g>
    <rect width="{WIDTH}" height="{HEIGHT}" fill="url(#vignette)"/>
    <rect width="{WIDTH}" height="{HEIGHT}" fill="#dff6ff" opacity="{impact_flash:.3f}"/>
    <rect width="{WIDTH}" height="{HEIGHT}" fill="#061222" opacity="{cold_fade:.3f}"/>
    <rect x="0" y="0" width="{WIDTH}" height="30" fill="#020509"/>
    <rect x="0" y="510" width="{WIDTH}" height="30" fill="#020509"/>
    </svg>'''


def _mix_color(first: str, second: str, amount: float) -> str:
    a = tuple(int(first[i : i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(second[i : i + 2], 16) for i in (1, 3, 5))
    rgb = tuple(round(lerp(a[i], b[i], amount)) for i in range(3))
    return "#" + "".join(f"{value:02x}" for value in rgb)


def _gradient_commands(top: str, bottom: str, steps: int = 42) -> list[str]:
    commands: list[str] = []
    for step in range(steps):
        y1 = HEIGHT * step / steps
        y2 = HEIGHT * (step + 1) / steps + 1
        color = _mix_color(top, bottom, step / max(steps - 1, 1))
        commands.append(f'fill "{color}" stroke none rectangle 0,{y1:.2f} {WIDTH},{y2:.2f}')
    return commands


def _parse_transform(value: str) -> list[str]:
    commands: list[str] = []
    rest = value.strip()
    while rest:
        if "(" not in rest or ")" not in rest:
            break
        name, tail = rest.split("(", 1)
        raw, rest = tail.split(")", 1)
        name = name.strip()
        values = [float(part) for part in raw.replace(",", " ").split()]
        if name == "translate":
            commands.append(f"translate {values[0]:.4f},{(values[1] if len(values) > 1 else 0):.4f}")
        elif name == "scale":
            commands.append(f"scale {values[0]:.5f},{(values[1] if len(values) > 1 else values[0]):.5f}")
        elif name == "rotate":
            if len(values) >= 3:
                commands.extend(
                    [
                        f"translate {values[1]:.4f},{values[2]:.4f}",
                        f"rotate {values[0]:.4f}",
                        f"translate {-values[1]:.4f},{-values[2]:.4f}",
                    ]
                )
            else:
                commands.append(f"rotate {values[0]:.4f}")
        rest = rest.strip()
    return commands


def svg_to_mvg(svg: str) -> str:
    """Translate the small SVG subset used here to ImageMagick's native MVG.

    This keeps rendering entirely local and procedural; no SVG browser or
    external image assets are required in the execution environment.
    """
    root = ET.fromstring(svg)
    commands = [f"viewbox 0 0 {WIDTH} {HEIGHT}"]
    commands.extend(_gradient_commands("#071729", "#b3cad7"))
    paint_map = {
        "url(#sky)": "none",
        "url(#snowGround)": "#bdd2de",
        "url(#parka)": "#39534a",
        "url(#fur)": "#342925",
        "url(#steel)": "#becfd5",
        "url(#vignette)": "none",
    }

    def emit(
        element: ET.Element,
        inherited_fill: str = "black",
        inherited_stroke: str = "none",
        inherited_opacity: float = 1.0,
    ) -> None:
        tag = element.tag.rsplit("}", 1)[-1]
        if tag in {"defs", "linearGradient", "radialGradient", "stop", "filter", "feGaussianBlur", "feOffset", "feMerge", "feMergeNode"}:
            return
        attrs = element.attrib
        commands.append("push graphic-context")

        if "transform" in attrs:
            commands.extend(_parse_transform(attrs["transform"]))

        source_fill = attrs.get("fill", inherited_fill)
        source_stroke = attrs.get("stroke", inherited_stroke)
        fill = paint_map.get(source_fill, source_fill)
        stroke = paint_map.get(source_stroke, source_stroke)
        opacity = inherited_opacity * float(attrs.get("opacity", "1"))
        commands.append(f'fill "{fill}"')
        commands.append(f'stroke "{stroke}"')
        commands.append(f"fill-opacity {opacity:.5f}")
        commands.append(f"stroke-opacity {opacity:.5f}")
        if "stroke-width" in attrs:
            commands.append(f"stroke-width {float(attrs['stroke-width']):.4f}")
        if "stroke-linecap" in attrs:
            commands.append(f"stroke-linecap {attrs['stroke-linecap']}")
        if "stroke-linejoin" in attrs:
            commands.append(f"stroke-linejoin {attrs['stroke-linejoin']}")

        if tag in {"svg", "g"}:
            for child in element:
                emit(child, source_fill, source_stroke, opacity)
        elif tag == "rect":
            x = float(attrs.get("x", 0))
            y = float(attrs.get("y", 0))
            width = float(attrs["width"])
            height = float(attrs["height"])
            source_fill = attrs.get("fill", "black")
            if source_fill == "url(#sky)":
                pass  # The high-quality banded sky was emitted first.
            elif source_fill == "url(#vignette)":
                # Layered translucent borders create a soft cinematic vignette.
                for band in range(12):
                    inset = band * 4.5
                    alpha = 0.055 * (1.0 - band / 12)
                    commands.append(
                        f'fill none stroke "#020711" stroke-opacity {alpha:.4f} stroke-width 10 '
                        f'rectangle {inset:.2f},{inset:.2f} {WIDTH-inset:.2f},{HEIGHT-inset:.2f}'
                    )
            else:
                commands.append(f"rectangle {x:.3f},{y:.3f} {x+width:.3f},{y+height:.3f}")
        elif tag == "circle":
            cx = float(attrs["cx"])
            cy = float(attrs["cy"])
            r = float(attrs["r"])
            commands.append(f"circle {cx:.3f},{cy:.3f} {cx+r:.3f},{cy:.3f}")
        elif tag == "ellipse":
            commands.append(
                f"ellipse {float(attrs['cx']):.3f},{float(attrs['cy']):.3f} "
                f"{float(attrs['rx']):.3f},{float(attrs['ry']):.3f} 0,360"
            )
        elif tag == "line":
            commands.append(
                f"line {float(attrs['x1']):.3f},{float(attrs['y1']):.3f} "
                f"{float(attrs['x2']):.3f},{float(attrs['y2']):.3f}"
            )
        elif tag == "polygon":
            points = " ".join(attrs["points"].replace(",", " ").split())
            values = points.split()
            paired = " ".join(f"{values[i]},{values[i+1]}" for i in range(0, len(values), 2))
            commands.append(f"polygon {paired}")
        elif tag == "path":
            path = attrs["d"].replace("'", "")
            commands.append(f"path '{path}'")

        commands.append("pop graphic-context")

    # Skip the SVG root's own default black fill context and emit its children.
    for child in root:
        emit(child)
    return "\n".join(commands) + "\n"


def render_jpeg(svg: str, quality: int = 84) -> bytes:
    cmd = [
        "convert",
        "mvg:-",
        "-strip",
        "-sampling-factor",
        "4:2:0",
        "-quality",
        str(quality),
        "jpeg:-",
    ]
    process = subprocess.run(
        cmd,
        input=svg_to_mvg(svg).encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode != 0 or not process.stdout.startswith(b"\xff\xd8"):
        raise RuntimeError(process.stderr.decode("utf-8", errors="replace"))
    return process.stdout


def add_tone(buffer: list[float], start: float, duration: float, frequency: float, volume: float, decay: float = 3.0) -> None:
    begin = max(0, int(start * SAMPLE_RATE))
    end = min(len(buffer), int((start + duration) * SAMPLE_RATE))
    for i in range(begin, end):
        age = i / SAMPLE_RATE - start
        env = max(0.0, math.sin(math.pi * age / duration)) ** 0.65 * math.exp(-decay * age / max(duration, 0.001))
        buffer[i] += math.sin(2 * math.pi * frequency * age) * env * volume


def soundtrack() -> bytes:
    total = int(DURATION * SAMPLE_RATE)
    samples = [0.0] * total
    rng = random.Random(9917)
    wind_state = 0.0
    rumble_state = 0.0

    # Filtered noise: constant arctic wind and low cinematic tension.
    for i in range(total):
        n = rng.uniform(-1.0, 1.0)
        wind_state = wind_state * 0.985 + n * 0.015
        rumble_state = rumble_state * 0.998 + n * 0.002
        t = i / SAMPLE_RATE
        swell = 0.65 + 0.35 * math.sin(t * 0.42)
        samples[i] = wind_state * 0.30 * swell + rumble_state * 0.44
        samples[i] += math.sin(2 * math.pi * (42 + 5 * math.sin(t * 0.25)) * t) * 0.018

    # Footfalls during the chase.
    for t in [0.48 + n * 0.47 for n in range(10)]:
        add_tone(samples, t, 0.16, 54, 0.34, 5.0)
    # Bear footfalls, lower and slower.
    for t in [0.72 + n * 0.58 for n in range(8)]:
        add_tone(samples, t, 0.22, 36, 0.42, 5.5)

    # Growls are roughened low tones.
    for start, dur, vol in [(2.9, 1.15, 0.22), (4.35, 1.3, 0.30), (8.0, 1.0, 0.32), (9.45, 1.25, 0.34)]:
        begin, end = int(start * SAMPLE_RATE), int((start + dur) * SAMPLE_RATE)
        phase = 0.0
        for i in range(begin, min(end, total)):
            age = (i - begin) / SAMPLE_RATE
            env = max(0.0, math.sin(math.pi * age / dur)) ** 0.45
            freq = 57 + 12 * math.sin(age * 17)
            phase += 2 * math.pi * freq / SAMPLE_RATE
            samples[i] += (math.sin(phase) + 0.32 * rng.uniform(-1, 1)) * env * vol

    # Bite, struggle, axe whoosh, and impact.
    add_tone(samples, 5.17, 0.30, 46, 0.66, 7.0)
    add_tone(samples, 6.12, 0.24, 62, 0.38, 5.0)
    whoosh_start, whoosh_end = int(8.55 * SAMPLE_RATE), int(9.42 * SAMPLE_RATE)
    state = 0.0
    for i in range(whoosh_start, whoosh_end):
        age = (i - whoosh_start) / max(1, whoosh_end - whoosh_start)
        state = state * 0.82 + rng.uniform(-1, 1) * 0.18
        samples[i] += state * (age ** 1.8) * 0.42
    add_tone(samples, 9.43, 0.42, 48, 0.88, 8.0)
    add_tone(samples, 9.43, 0.18, 118, 0.48, 10.0)

    # Fade the final second.
    pcm = array("h")
    for i, value in enumerate(samples):
        t = i / SAMPLE_RATE
        fade = clamp((DURATION - t) / 0.85) if t > DURATION - 0.85 else 1.0
        shaped = math.tanh(value * 1.7) * 0.74 * fade
        pcm.append(int(clamp(shaped, -1.0, 1.0) * 32767))
    if sys.byteorder != "little":
        pcm.byteswap()
    return pcm.tobytes()


def stream_header(fcc_type: bytes, handler: bytes, scale: int, rate: int, length: int, suggested: int, sample_size: int, quality: int = 0xFFFFFFFF) -> bytes:
    return struct.pack(
        "<4s4sIHHIIIIIIIIhhhh",
        fcc_type,
        handler,
        0,
        0,
        0,
        0,
        scale,
        rate,
        0,
        length,
        suggested,
        quality,
        sample_size,
        0,
        0,
        WIDTH if fcc_type == b"vids" else 0,
        HEIGHT if fcc_type == b"vids" else 0,
    )


def avi_header() -> bytes:
    max_frame = WIDTH * HEIGHT * 3
    avih = struct.pack(
        "<IIIIIIIIII4I",
        round(1_000_000 / FPS),
        max_frame * FPS,
        0,
        0x10,  # AVIF_HASINDEX
        FRAMES,
        0,
        2,
        max_frame,
        WIDTH,
        HEIGHT,
        0,
        0,
        0,
        0,
    )
    video_strh = stream_header(b"vids", b"MJPG", 1, FPS, FRAMES, max_frame, 0)
    video_strf = struct.pack(
        "<IiiHH4sIiiII",
        40,
        WIDTH,
        HEIGHT,
        1,
        24,
        b"MJPG",
        max_frame,
        0,
        0,
        0,
        0,
    )
    block_align = 2
    total_samples = int(DURATION * SAMPLE_RATE)
    audio_strh = stream_header(
        b"auds",
        b"\0\0\0\0",
        block_align,
        SAMPLE_RATE * block_align,
        total_samples,
        int(SAMPLE_RATE / FPS) * block_align + 4,
        block_align,
    )
    audio_strf = struct.pack(
        "<HHIIHH",
        1,
        1,
        SAMPLE_RATE,
        SAMPLE_RATE * block_align,
        block_align,
        16,
    )
    video_list = list_chunk(b"strl", chunk(b"strh", video_strh) + chunk(b"strf", video_strf))
    audio_list = list_chunk(b"strl", chunk(b"strh", audio_strh) + chunk(b"strf", audio_strf))
    return list_chunk(b"hdrl", chunk(b"avih", avih) + video_list + audio_list)


def create_video(output: Path, quality: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    audio = soundtrack()
    index: list[tuple[bytes, int, int, int]] = []

    with output.open("wb+") as handle:
        handle.write(b"RIFF")
        riff_size_pos = handle.tell()
        handle.write(struct.pack("<I", 0))
        handle.write(b"AVI ")
        handle.write(avi_header())

        handle.write(b"LIST")
        movi_size_pos = handle.tell()
        handle.write(struct.pack("<I", 0))
        movi_fourcc_pos = handle.tell()
        handle.write(b"movi")

        for frame in range(FRAMES):
            jpeg = render_jpeg(frame_svg(frame), quality)
            video_chunk_pos = handle.tell()
            handle.write(chunk(b"00dc", jpeg))
            index.append((b"00dc", 0x10, video_chunk_pos - movi_fourcc_pos, len(jpeg)))

            sample_start = (frame * SAMPLE_RATE) // FPS
            sample_end = ((frame + 1) * SAMPLE_RATE) // FPS
            pcm = audio[sample_start * 2 : sample_end * 2]
            audio_chunk_pos = handle.tell()
            handle.write(chunk(b"01wb", pcm))
            index.append((b"01wb", 0, audio_chunk_pos - movi_fourcc_pos, len(pcm)))

            if frame % FPS == 0 or frame == FRAMES - 1:
                print(f"Rendered {frame + 1:3d}/{FRAMES} frames", flush=True)

        movi_end = handle.tell()
        handle.seek(movi_size_pos)
        handle.write(struct.pack("<I", movi_end - movi_size_pos - 4))
        handle.seek(movi_end)

        idx_payload = b"".join(
            struct.pack("<4sIII", chunk_id, flags, offset, size)
            for chunk_id, flags, offset, size in index
        )
        handle.write(chunk(b"idx1", idx_payload))
        file_end = handle.tell()
        handle.seek(riff_size_pos)
        handle.write(struct.pack("<I", file_end - 8))

    print(f"Created {output} ({output.stat().st_size / 1024 / 1024:.1f} MiB)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output", type=Path, default=Path("cinematic-snow-survival.avi"))
    parser.add_argument("--quality", type=int, default=84, choices=range(55, 96), metavar="55..95")
    parser.add_argument("--preview-frame", type=int, help="render just one JPEG frame for debugging")
    args = parser.parse_args()

    if args.preview_frame is not None:
        frame = max(0, min(FRAMES - 1, args.preview_frame))
        target = args.output.with_suffix(".jpg")
        target.write_bytes(render_jpeg(frame_svg(frame), args.quality))
        print(f"Created {target}")
        return

    create_video(args.output, args.quality)


if __name__ == "__main__":
    main()
