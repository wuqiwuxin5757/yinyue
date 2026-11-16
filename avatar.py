"""银月的桌面形象：右下角任务栏上方常驻一个原创 Q 版小人，被叫到时头顶弹出对话浮窗。

- 形象是代码画出来的原创设计（银发、狐耳、狐尾、新月发饰），不使用任何官方美术。
- 用逐像素透明的分层窗口（UpdateLayeredWindow），边缘平滑没有色边。
- assistant.py 通过本机 UDP（127.0.0.1:8770）发事件过来：
    wake / user(文字) / thinking / tool(说明) / reply(文字) / speak_end / end
- 待机：一直有轻微呼吸起伏，几秒眨一次眼，每隔十几到几十秒做个小动作；
  一个小时没人理就趴下睡觉，再被叫醒时醒来。
- 可以用鼠标拖动小人换位置；右键小人可以隐藏浮窗或退出。
"""
import ctypes
import json
import math
import random
import socket
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import dance_choreo

ctypes.windll.shcore.SetProcessDpiAwareness(2)
user32, gdi32, kernel32 = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.kernel32

PORT = 8770
FRAME_PORT = 8771   # 本机帧服务（手机网页看银月用）
HIDDEN_FRAME = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
EDGE_TURN_SECONDS = 0.28
EDGE_STEP_END = 1.82
EDGE_PEEK_END = 2.28
EDGE_PEEK_HOLD_END = 2.70
EDGE_WITHDRAW_SECONDS = 3.17
EDGE_TURNS = {"left": -105, "right": 75, "bottom": -15, "top": 165}
FAREWELL_TIMES = {"wave": 1.65, "bow": 1.55, "moon": 1.85, "wolf": 1.50}
SAVE_FILE = __import__("pathlib").Path(__file__).parent / "data" / "avatar.json"  # 记住穿的哪套衣服
SCALE = user32.GetDpiForSystem() / 96          # 4K 屏开了缩放时，按缩放比例放大
try:
    _STYLE = json.loads((__import__("pathlib").Path(__file__).parent / "config.json").read_text(encoding="utf-8")).get("avatar_style", "vrm")
except Exception:
    _STYLE = "2d"
# 小人窗口大小（右边留出尾巴摆动的空间）；VRM 模型是正常身材比例，窗口高一些
VRM_DISPLAY_SCALE = 1.2
W, H = tuple(round(size * SCALE * VRM_DISPLAY_SCALE) for size in (185, 291)) if _STYLE == "vrm" else (int(186 * SCALE), int(210 * SCALE))
DANCE_PAD_X, DANCE_PAD_Y = round(112 * SCALE), round(80 * SCALE)
SS = 3                                          # 超采样倍数，画大再缩小，边缘更平滑
FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"

# 配色
HAIR, HAIR_SHADE, HAIR_LIGHT = (226, 231, 242), (176, 186, 210), (250, 251, 255)
SKIN, BLUSH = (255, 236, 226), (255, 170, 180)
EYE, EYE_DARK = (120, 110, 220), (60, 50, 140)

# 换装：每套衣服一组配色 + 一款发饰。robe 衣服主色, shade 衣褶阴影, trim 滚边, inner 里衬（领口、袖口露出来的那层）,
# sash 腰带, accent 点缀色（金饰等）, pattern 衣摆暗纹, cape 披风颜色（没有就是 None）, hair 发饰款式
OUTFITS = {
    "月白仙裙": dict(robe=(250, 250, 255), shade=(222, 226, 242), trim=(160, 172, 230), inner=(214, 222, 252),
                 sash=(150, 170, 235), accent=(245, 210, 110), pattern=(190, 200, 240), cape=None, hair="moon"),
    "霜华宫装": dict(robe=(248, 251, 255), shade=(214, 226, 240), trim=(120, 160, 210), inner=(200, 225, 248),
                 sash=(110, 150, 205), accent=(205, 215, 230), pattern=(150, 185, 225), cape=None, hair="buyao"),
    "樱雪春衫": dict(robe=(255, 251, 252), shade=(242, 222, 228), trim=(240, 150, 175), inner=(255, 220, 230),
                 sash=(245, 160, 185), accent=(255, 205, 120), pattern=(248, 185, 205), cape=None, hair="flower"),
    "青竹侠衣": dict(robe=(250, 253, 250), shade=(220, 236, 226), trim=(90, 170, 130), inner=(205, 240, 220),
                 sash=(80, 155, 120), accent=(230, 200, 110), pattern=(150, 205, 175), cape=None, hair="ribbon"),
    "夜月玄衣": dict(robe=(52, 56, 92), shade=(36, 40, 70), trim=(230, 200, 120), inner=(240, 240, 250),
                 sash=(230, 200, 120), accent=(250, 220, 130), pattern=(120, 125, 180), cape=None, hair="star_moon"),
    "冬雪斗篷": dict(robe=(250, 250, 255), shade=(222, 226, 242), trim=(200, 70, 80), inner=(255, 236, 236),
                 sash=(200, 70, 80), accent=(245, 210, 110), pattern=(235, 170, 175), cape=(205, 60, 70), hair="pompom"),
}
DEFAULT_OUTFIT = "月白仙裙"
if _STYLE == "vrm":
    _alias = {n: "月白仙裙" for n in ['魔法少女', '白魔法少女', '粉色连衣裙', '白色连衣裙白丝', '黑色连衣裙黑丝', '浅蓝短袖短裙白丝', '白短袖黑短裙黑丝', '酒红短袖黑丝', '运动外套黑短裙黑丝', '韩系灰格裙', '韩系奶咖格裙白丝', '韩系粉格裙', '韩系黑白格裙黑丝', '韩系蓝格裙']}  # 2D/3D 退回时统一用白色配色
    OUTFITS = {k: OUTFITS[v] for k, v in _alias.items()}
    DEFAULT_OUTFIT = "浅蓝短袖短裙白丝"
EAR_IN, GOLD, LINE = (255, 196, 210), (245, 210, 110), (90, 90, 130)


# ======================================================================= 画小人

def _rot(img, angle, center):
    return img.rotate(angle, resample=Image.BICUBIC, center=center)


def draw_yinyue(p: dict) -> Image.Image:
    """p 里的参数决定这一帧的姿态：bob 上下起伏, tail 尾巴角度, tilt 头部倾斜, ear 耳朵抖动,
    eyes(open/closed/happy/sleep), mouth(smile/open/o/flat), arm(down/wave/up), wave 挥手角度, extra(zzz/think/note/sparkle)"""
    k = SCALE * SS
    cw, ch = W * SS, H * SS
    f = lambda *xs: [x * k for x in xs]  # 设计稿坐标（170x210）→ 实际像素
    dy = p.get("bob", 0)

    img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))

    # ---- 尾巴（单独一层，绕尾根旋转）
    tail = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    d = ImageDraw.Draw(tail)
    base = (112, 178 + dy)
    blobs = []
    for i in range(25):  # 一串密集的圆连成蓬松的大尾巴：先画一圈深色当轮廓，再填浅色
        t = i / 24
        cx = base[0] + 34 * t + 6 * math.sin(t * 3)
        cy = base[1] - 66 * t ** 1.2
        blobs.append((cx, cy, 8 + 17 * math.sin(t * math.pi * 0.8)))
    for cx, cy, r in blobs:
        d.ellipse(f(cx - r - 1.2, cy - r - 1.2, cx + r + 1.2, cy + r + 1.2), fill=HAIR_SHADE + (255,))
    for i, (cx, cy, r) in enumerate(blobs):
        d.ellipse(f(cx - r, cy - r, cx + r, cy + r), fill=(HAIR if i < 19 else HAIR_LIGHT) + (255,))
    cx, cy, r = blobs[-1]
    d.ellipse(f(cx - r * 0.7, cy - r * 0.7, cx + r * 0.5, cy + r * 0.5), fill=(255, 255, 255, 255))  # 尾巴尖
    for t in (0.35, 0.6):  # 两道毛发纹理
        cx, cy, r = blobs[int(t * 24)]
        d.arc(f(cx - r * 0.6, cy - r * 0.6, cx + r * 0.6, cy + r * 0.6), 200, 290, fill=HAIR_SHADE + (255,), width=int(1.2 * k))
    img.alpha_composite(_rot(tail, p.get("tail", 0), (base[0] * k, base[1] * k)))

    d = ImageDraw.Draw(img)
    # ---- 后面的长发
    d.rounded_rectangle(f(48, 92 + dy, 122, 182 + dy), radius=30 * k, fill=HAIR_SHADE + (255,))
    o = OUTFITS.get(p.get("outfit"), OUTFITS[DEFAULT_OUTFIT])
    A = lambda c, a=255: tuple(c) + (a,)
    lw = max(1, int(1.2 * k))

    # ---- 披风（在身体后面）
    if o["cape"]:
        d.polygon(f(56, 140 + dy, 114, 140 + dy, 130, 200 + dy, 40, 200 + dy), fill=A(o["cape"]))
        d.line(f(40, 200 + dy, 130, 200 + dy), fill=A(tuple(max(0, c - 40) for c in o["cape"])), width=int(2 * k))

    # ---- 裙摆：上窄下宽，底边是一道柔和的弧线
    hem = [(60, 140), (110, 140), (124, 190), (128, 199), (110, 204), (85, 206), (60, 204), (42, 199), (46, 190)]
    d.polygon(f(*[c for x, y in hem for c in (x, y + dy)]), fill=A(o["robe"]), outline=A(o["shade"]))
    for x0, x1 in ((72, 62), (80, 76), (96, 100), (102, 112)):  # 衣褶
        d.line(f(x0, 168 + dy, x1, 202 + dy), fill=A(o["shade"]), width=lw)
    # 衣摆滚边 + 暗纹（一排小祥云）
    d.line(f(44, 196 + dy, 85, 201 + dy, 126, 196 + dy), fill=A(o["trim"]), width=int(2 * k))
    for cx in range(54, 120, 13):
        cy = 191 + dy + (3 if abs(cx - 85) < 20 else 1)
        d.arc(f(cx - 4, cy - 3, cx + 4, cy + 3), 180, 360, fill=A(o["pattern"]), width=lw)
        d.arc(f(cx, cy - 2, cx + 6, cy + 3), 180, 340, fill=A(o["pattern"]), width=lw)
    # 交叠的前襟
    d.line(f(85, 152 + dy, 94, 205 + dy), fill=A(o["trim"]), width=int(1.5 * k))

    # ---- 上身 + 交领（右衽：左襟压右襟）
    d.rounded_rectangle(f(61, 138 + dy, 109, 162 + dy), radius=6 * k, fill=A(o["robe"]))
    d.polygon(f(76, 139 + dy, 94, 139 + dy, 85, 152 + dy), fill=A(o["inner"]))                       # 领口露出的里衬
    d.line(f(72, 139 + dy, 88, 157 + dy), fill=A(o["trim"]), width=int(3 * k))                       # 右领
    d.line(f(98, 139 + dy, 84, 155 + dy), fill=A(o["trim"]), width=int(3 * k))                       # 左领（压在上面）
    d.line(f(73, 138 + dy, 88.5, 155.5 + dy), fill=A(o["accent"]), width=lw)                          # 领边金线
    d.line(f(97, 138 + dy, 84.5, 153.5 + dy), fill=A(o["accent"]), width=lw)

    # ---- 腰带 + 蝴蝶结飘带 + 新月玉佩
    sway = p.get("tail", 0) * 0.15
    d.rounded_rectangle(f(60, 157 + dy, 110, 166 + dy), radius=3 * k, fill=A(o["sash"]))
    d.line(f(60, 158 + dy, 110, 158 + dy), fill=A(o["accent"]), width=lw)
    for side in (-1, 1):  # 两条飘带
        bx = 85 + 3 * side
        d.polygon(f(bx, 164 + dy, bx + 4 * side, 164 + dy, bx + 7 * side + sway, 186 + dy, bx + 2 * side + sway, 184 + dy),
                  fill=A(o["sash"]))
    d.ellipse(f(80, 157 + dy, 90, 167 + dy), fill=A(o["sash"]), outline=A(o["accent"]), width=lw)   # 结
    px = 70 + sway
    d.line(f(70, 166 + dy, px, 176 + dy), fill=A(o["accent"]), width=lw)                             # 玉佩挂绳
    d.ellipse(f(px - 4, 175 + dy, px + 4, 183 + dy), fill=A(o["inner"]), outline=A(o["trim"]), width=lw)
    d.line(f(px, 183 + dy, px, 190 + dy), fill=A(o["trim"]), width=int(1.6 * k))                    # 流苏

    # ---- 广袖：从肩膀往外越来越宽，袖口下垂
    def sleeve(x0, y0, angle, length=28):
        a = math.radians(angle)
        ux, uy = math.sin(a), math.cos(a)          # 手臂方向
        nx, ny = -uy, ux                           # 垂直方向
        x1, y1 = x0 + length * ux, y0 + length * uy
        hand = (x1 + 3 * ux, y1 + 3 * uy)
        d.ellipse(f(hand[0] - 4.5, hand[1] - 4.5, hand[0] + 4.5, hand[1] + 4.5), fill=A(SKIN))
        pts = [(x0 + 5 * nx, y0 + 5 * ny), (x1 + 11 * nx, y1 + 11 * ny),
               (x1 + 9 * nx + 2, y1 + 9 * ny + 12), (x1 - 11 * nx, y1 - 11 * ny), (x0 - 5 * nx, y0 - 5 * ny)]
        d.polygon(f(*[c for pt in pts for c in pt]), fill=A(o["robe"]), outline=A(o["shade"]))
        d.line(f(x1 + 11 * nx, y1 + 11 * ny, x1 - 11 * nx, y1 - 11 * ny), fill=A(o["trim"]), width=int(2.4 * k))  # 袖口滚边
        d.line(f(x1 + 9 * nx, y1 + 9 * ny, x1 - 9 * nx, y1 - 9 * ny), fill=A(o["inner"]), width=lw)

    pose = p.get("arm", "down")
    if pose == "up":                                         # 伸懒腰：双手举高
        sleeve(106, 146 + dy, 160)
        sleeve(64, 146 + dy, 200)
    else:
        sleeve(63, 145 + dy, -14, 26)                        # 左手向外自然下垂
        if pose == "wave":
            sleeve(107, 145 + dy, 180 - 40 + p.get("wave", 0), 26)  # 右手举起挥动
        else:
            sleeve(107, 145 + dy, 14, 26)

    # ---- 头部（单独一层，绕脖子转动）
    head = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    h = ImageDraw.Draw(head)
    hx, hy = 85, 92 + dy

    def ear(cx, side):
        tw = p.get("ear", 0) * side
        tip = (cx + 6 * side + tw, hy - 66)
        h.polygon(f(cx - 14, hy - 30, cx + 14, hy - 30, *tip), fill=HAIR + (255,), outline=HAIR_SHADE + (255,))
        h.polygon(f(cx - 7, hy - 33, cx + 7, hy - 33, tip[0] - 1 * side, tip[1] + 14), fill=EAR_IN + (255,))
    ear(hx - 26, -1)
    ear(hx + 26, 1)

    h.ellipse(f(hx - 44, hy - 44, hx + 44, hy + 40), fill=HAIR + (255,), outline=HAIR_SHADE + (255,), width=int(k))  # 头发外轮廓
    face = [(hx + 35 * math.cos(a), hy + 6 - 34 * math.sin(a)) for a in [i * math.pi / 18 for i in range(19)]]  # 上半圆
    face += [(hx - 33, hy + 16), (hx - 24, hy + 32), (hx - 9, hy + 42), (hx, hy + 44),          # 往下收成尖下巴
             (hx + 9, hy + 42), (hx + 24, hy + 32), (hx + 33, hy + 16)]
    h.polygon(f(*[c for pt in face for c in pt]), fill=SKIN + (255,))                                           # 瓜子脸
    # 刘海
    for bx, bw, bl in ((-34, 22, 30), (-14, 22, 34), (6, 22, 32), (24, 18, 26)):
        h.polygon(f(hx + bx, hy - 34, hx + bx + bw, hy - 34, hx + bx + bw * 0.55, hy - 34 + bl), fill=HAIR + (255,))
    h.chord(f(hx - 40, hy - 46, hx + 40, hy - 6), 180, 360, fill=HAIR + (255,))
    h.arc(f(hx - 30, hy - 42, hx + 10, hy - 20), 200, 300, fill=HAIR_LIGHT + (255,), width=int(2 * k))  # 头发高光
    # 两侧鬓发
    h.polygon(f(hx - 42, hy - 10, hx - 30, hy - 10, hx - 34, hy + 46, hx - 46, hy + 40), fill=HAIR + (255,))
    h.polygon(f(hx + 42, hy - 10, hx + 30, hy - 10, hx + 34, hy + 46, hx + 46, hy + 40), fill=HAIR + (255,))

    # 眼睛
    eyes = p.get("eyes", "open")
    lk = p.get("look", 0) * 3          # 眼珠左右看
    for i, ex in enumerate((hx - 15, hx + 15)):
        ey = hy + 8
        e = eyes
        if eyes == "wink":                # 眨一只眼：左眼闭，右眼睁
            e = "happy" if i == 0 else "open"
        if e == "open":
            h.ellipse(f(ex - 8, ey - 10, ex + 8, ey + 10), fill=EYE + (255,), outline=EYE_DARK + (255,), width=int(k))
            h.ellipse(f(ex - 5 + lk, ey - 4, ex + 5 + lk, ey + 8), fill=EYE_DARK + (255,))
            h.ellipse(f(ex - 5 + lk, ey - 8, ex + 1 + lk, ey - 2), fill=(255, 255, 255, 255))
            h.ellipse(f(ex + 2 + lk, ey + 3, ex + 5 + lk, ey + 6), fill=(255, 255, 255, 230))
            h.line(f(ex - 9, ey - 11, ex + 8, ey - 13), fill=LINE + (255,), width=int(1.6 * k))  # 上眼线
        elif e == "happy":
            h.arc(f(ex - 8, ey - 4, ex + 8, ey + 10), 200, 340, fill=EYE_DARK + (255,), width=int(2.5 * k))
        else:  # closed / sleep
            h.arc(f(ex - 8, ey - 6, ex + 8, ey + 6), 20, 160, fill=EYE_DARK + (255,), width=int(2.2 * k))
    # 腮红
    for bx in (hx - 24, hx + 24):
        h.ellipse(f(bx - 7, hy + 20, bx + 7, hy + 26), fill=BLUSH + (p.get("blush", 110),))
    # 嘴
    mouth = p.get("mouth", "smile")
    my = hy + 26
    if mouth == "yawn":
        h.ellipse(f(hx - 6, my - 4, hx + 6, my + 9), fill=(190, 80, 100, 255))
    elif mouth == "open":
        h.ellipse(f(hx - 5, my - 3, hx + 5, my + 6), fill=(200, 90, 110, 255))
    elif mouth == "o":
        h.ellipse(f(hx - 3, my - 2, hx + 3, my + 4), fill=(200, 90, 110, 255))
    elif mouth == "flat":
        h.line(f(hx - 4, my, hx + 4, my), fill=LINE + (255,), width=int(1.5 * k))
    else:
        h.arc(f(hx - 6, my - 5, hx + 6, my + 3), 20, 160, fill=LINE + (255,), width=int(1.6 * k))
    # 发饰（随衣服换）
    mx, my2 = hx + 30, hy - 30
    acc = o["hair"]
    if acc in ("moon", "star_moon"):
        h.ellipse(f(mx - 9, my2 - 9, mx + 9, my2 + 9), fill=A(o["accent"]))
        h.ellipse(f(mx - 4, my2 - 12, mx + 12, my2 + 4), fill=(0, 0, 0, 0))
        h.ellipse(f(mx - 13, my2 + 4, mx - 9, my2 + 8), fill=(160, 170, 240, 255))   # 小宝石
        if acc == "star_moon":
            sx, sy = hx - 30, hy - 32
            h.polygon(f(sx, sy - 6, sx + 2, sy - 2, sx + 6, sy, sx + 2, sy + 2, sx, sy + 6, sx - 2, sy + 2, sx - 6, sy, sx - 2, sy - 2),
                      fill=A(o["accent"]))
    elif acc == "buyao":  # 步摇：一支发簪，下面垂几串珠子
        h.line(f(mx - 12, my2 + 4, mx + 12, my2 - 10), fill=A(o["accent"]), width=int(2 * k))
        h.ellipse(f(mx - 6, my2 - 8, mx + 4, my2 + 2), fill=A(o["trim"]), outline=A(o["accent"]), width=lw)
        swing = p.get("tail", 0) * 0.2
        for i, ln in enumerate((10, 14, 10)):
            bx = mx - 4 + i * 4
            h.line(f(bx, my2 + 2, bx + swing, my2 + 2 + ln), fill=A(o["accent"]), width=lw)
            h.ellipse(f(bx + swing - 2, my2 + ln, bx + swing + 2, my2 + ln + 4), fill=A(o["pattern"]))
    elif acc == "flower":  # 一朵小花
        for i in range(5):
            a = math.radians(i * 72 - 90)
            cx, cy = mx + 6 * math.cos(a), my2 + 6 * math.sin(a)
            h.ellipse(f(cx - 5, cy - 5, cx + 5, cy + 5), fill=A(o["sash"]), outline=A(o["trim"]), width=lw)
        h.ellipse(f(mx - 3, my2 - 3, mx + 3, my2 + 3), fill=A(o["accent"]))
        h.ellipse(f(mx - 16, my2 + 6, mx - 10, my2 + 12), fill=A(o["inner"]), outline=A(o["trim"]), width=lw)  # 小花苞
    elif acc == "ribbon":  # 蝴蝶结发带
        h.polygon(f(mx, my2, mx - 12, my2 - 8, mx - 12, my2 + 8), fill=A(o["sash"]))
        h.polygon(f(mx, my2, mx + 12, my2 - 8, mx + 12, my2 + 8), fill=A(o["sash"]))
        h.ellipse(f(mx - 3, my2 - 3, mx + 3, my2 + 3), fill=A(o["trim"]))
        h.line(f(mx - 2, my2 + 2, mx - 6, my2 + 16), fill=A(o["sash"]), width=int(2 * k))
        h.line(f(mx + 2, my2 + 2, mx + 5, my2 + 15), fill=A(o["sash"]), width=int(2 * k))
    elif acc == "pompom":  # 冬天的毛绒球
        h.line(f(mx - 8, my2 + 6, mx + 4, my2 - 4), fill=A(o["trim"]), width=int(2 * k))
        for cx, cy in ((mx + 4, my2 - 5), (mx - 6, my2 + 4)):
            h.ellipse(f(cx - 6, cy - 6, cx + 6, cy + 6), fill=(255, 255, 255, 255), outline=A(o["shade"]), width=lw)

    img.alpha_composite(_rot(head, p.get("tilt", 0), (hx * k, (hy + 42) * k)))

    # ---- 附加效果
    d = ImageDraw.Draw(img)
    extra = p.get("extra")
    font = ImageFont.truetype(FONT_PATH, int(14 * k))
    if extra == "zzz":
        ph = p.get("phase", 0)
        for i, s in enumerate((10, 13, 16)):
            fz = ImageFont.truetype(FONT_PATH, int(s * k))
            yy = 40 - i * 16 - (ph * 6 % 6)
            d.text(f(128 + i * 9, yy), "z", font=fz, fill=(140, 150, 220, 220))
    elif extra == "think":
        n = int(p.get("phase", 0) * 3) % 4
        for i in range(n):
            d.ellipse(f(130 + i * 11, 26, 137 + i * 11, 33), fill=(140, 150, 220, 230))
    elif extra == "note":
        sym = ImageFont.truetype(r"C:\Windows\Fonts\seguisym.ttf", int(16 * k))  # 微软雅黑里没有音符
        d.text(f(128, 16 - (p.get("phase", 0) * 4 % 6)), "♪", font=sym, fill=(140, 150, 220, 230))
    elif extra == "heart":
        t = p.get("phase", 0) % 3 / 3
        cx, cy, r = 136, 34 - 14 * t, 4.5
        a = int(240 * (1 - t))
        d.ellipse(f(cx - 2 * r, cy - r, cx, cy + r), fill=(250, 120, 150, a))
        d.ellipse(f(cx, cy - r, cx + 2 * r, cy + r), fill=(250, 120, 150, a))
        d.polygon(f(cx - 2 * r + 0.4, cy + 1, cx + 2 * r - 0.4, cy + 1, cx, cy + 2.6 * r), fill=(250, 120, 150, a))
    elif extra == "sparkle":
        cx, cy = 140, 30
        d.polygon(f(cx, cy - 9, cx + 3, cy - 3, cx + 9, cy, cx + 3, cy + 3, cx, cy + 9, cx - 3, cy + 3, cx - 9, cy, cx - 3, cy - 3),
                  fill=GOLD + (240,))
    out = img.resize((W, H), Image.LANCZOS)
    if p.get("spin"):
        out = out.rotate(p["spin"], resample=Image.BICUBIC, center=(W / 2, H * 0.6))
    dx, jump = p.get("dx", 0), p.get("jump", 0)
    if dx or jump:  # 整体左右晃 / 往上蹦
        moved = Image.new("RGBA", out.size, (0, 0, 0, 0))
        moved.alpha_composite(out, (int(dx * SCALE), int(-jump * SCALE)))
        out = moved
    return out


def draw_wolf(phase: float, facing: int = -1, puff: float = 0.0) -> Image.Image:
    """四足奔跑的小银狼。phase 是步态相位（弧度），facing=-1 朝左跑，1 朝右跑。
    步态是小跑：左前腿和右后腿一组、右前腿和左后腿一组，两组交替迈步。puff>0 时叠加变身烟雾。"""
    k = SCALE * SS
    cw, ch = W * SS, H * SS
    img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ox, oy = 18, 100                                   # 狼在窗口里的位置（设计稿坐标，窗口 186x210）
    f = lambda *xs: [(x + (ox if i % 2 == 0 else oy)) * k for i, x in enumerate(xs)]
    bounce = 4 * abs(math.sin(phase))                  # 每迈一步身体起伏一下
    by = -bounce
    lw = max(1, int(1.2 * k))
    far, near = HAIR_SHADE, HAIR

    def leg(hip_x, offset, color):
        swing = math.sin(phase + offset)
        lift = max(0.0, math.cos(phase + offset)) * 7   # 往前摆时抬脚
        knee = (hip_x + 6 * swing, 74 + by - lift * 0.4)
        foot = (hip_x + 14 * swing, 92 - lift)
        d.line(f(hip_x, 62 + by, *knee), fill=color + (255,), width=int(7 * k))
        d.line(f(*knee, *foot), fill=color + (255,), width=int(6 * k))
        d.ellipse(f(foot[0] - 4, foot[1] - 3, foot[0] + 4, foot[1] + 3), fill=color + (255,))

    # 远侧两条腿（颜色深一点，在身体后面）
    leg(52, math.pi, far)          # 右前
    leg(106, 0, far)               # 左后
    # 尾巴：一串圆，随步伐甩动
    wag = 10 * math.sin(phase * 0.5)
    for i in range(14):
        t = i / 13
        cx, cy = 118 + 26 * t, 52 + by - 26 * t ** 1.4 + wag * t
        r = 6 + 9 * math.sin(t * math.pi * 0.85)
        d.ellipse(f(cx - r - 1, cy - r - 1, cx + r + 1, cy + r + 1), fill=HAIR_SHADE + (255,))
    for i in range(14):
        t = i / 13
        cx, cy = 118 + 26 * t, 52 + by - 26 * t ** 1.4 + wag * t
        r = 6 + 9 * math.sin(t * math.pi * 0.85)
        d.ellipse(f(cx - r, cy - r, cx + r, cy + r), fill=(HAIR if i < 11 else HAIR_LIGHT) + (255,))
    # 身体
    d.ellipse(f(40, 38 + by, 124, 72 + by), fill=near + (255,), outline=HAIR_SHADE + (255,), width=lw)
    d.ellipse(f(55, 56 + by, 112, 72 + by), fill=HAIR_LIGHT + (255,))                         # 肚皮
    d.arc(f(60, 40 + by, 110, 60 + by), 200, 330, fill=HAIR_SHADE + (255,), width=lw)         # 背上毛发纹理
    # 近侧两条腿
    leg(56, 0, near)               # 左前
    leg(110, math.pi, near)        # 右后
    # 脖子 + 头
    d.ellipse(f(30, 30 + by, 62, 62 + by), fill=near + (255,))
    hx, hy = 34, 34 + by
    d.polygon(f(hx - 2, hy - 10, hx + 2, hy - 30, hx + 12, hy - 12), fill=near + (255,), outline=HAIR_SHADE + (255,))
    d.polygon(f(hx + 1, hy - 12, hx + 3, hy - 24, hx + 9, hy - 13), fill=EAR_IN + (255,))
    d.polygon(f(hx + 10, hy - 12, hx + 18, hy - 30, hx + 22, hy - 8), fill=near + (255,), outline=HAIR_SHADE + (255,))
    d.ellipse(f(hx - 14, hy - 14, hx + 22, hy + 16), fill=near + (255,), outline=HAIR_SHADE + (255,), width=lw)
    d.ellipse(f(hx - 26, hy - 2, hx - 2, hy + 14), fill=near + (255,), outline=HAIR_SHADE + (255,), width=lw)  # 吻部
    d.ellipse(f(hx - 28, hy + 1, hx - 22, hy + 6), fill=(70, 70, 100, 255))                   # 鼻子
    d.ellipse(f(hx - 6, hy - 4, hx + 2, hy + 4), fill=EYE_DARK + (255,))                       # 眼睛
    d.ellipse(f(hx - 5, hy - 3, hx - 2, hy), fill=(255, 255, 255, 255))
    d.ellipse(f(hx + 2, hy + 6, hx + 10, hy + 10), fill=BLUSH + (120,))
    mx, my = hx + 8, hy - 9                                                                  # 额头的新月
    d.ellipse(f(mx - 4, my - 4, mx + 4, my + 4), fill=GOLD + (255,))
    d.ellipse(f(mx - 1, my - 6, mx + 6, my + 1), fill=near + (255,))
    # 跑起来扬起的小尘土
    if math.sin(phase) > 0.6:
        for i in range(3):
            cx, cy, r = 128 + i * 9, 92 - i * 2, 3 + i
            d.ellipse(f(cx - r, cy - r, cx + r, cy + r), fill=(200, 205, 225, 120 - i * 30))
    out = img.resize((W, H), Image.LANCZOS)
    if facing > 0:
        out = out.transpose(Image.FLIP_LEFT_RIGHT)
    if puff > 0:
        out.alpha_composite(draw_puff(puff))
    return out


def draw_puff(t: float) -> Image.Image:
    """变身时的一团烟雾，t 从 0 到 1：先鼓起来再散开。"""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    a = int(230 * (1 - abs(t * 2 - 1)))
    rng = random.Random(7)
    for _ in range(16):   # 光雾铺满全身（从头到腿），换衣服那一下完全挡住
        cx = W * (0.5 + rng.uniform(-0.3, 0.3))
        cy = H * (0.5 + rng.uniform(-0.38, 0.38))
        r = W * (0.1 + 0.16 * t) * rng.uniform(0.75, 1.25)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(245, 246, 255, a), outline=(200, 206, 235, a))
    for _ in range(9):  # 几颗小星星
        cx, cy = W * rng.uniform(0.15, 0.85), H * rng.uniform(0.1, 0.9)
        r = 4 * SCALE
        d.polygon((cx, cy - r * 2, cx + r * 0.5, cy - r * 0.5, cx + r * 2, cy, cx + r * 0.5, cy + r * 0.5,
                   cx, cy + r * 2, cx - r * 0.5, cy + r * 0.5, cx - r * 2, cy, cx - r * 0.5, cy - r * 0.5),
                  fill=GOLD + (a,))
    return img


# ======================================================================= 浮窗

BUBBLE_DETAIL = "点击展开全文"


def draw_bubble(lines: list[tuple[str, str]], width: int) -> Image.Image:
    """lines: [(角色, 文字)]，角色是 user / think / reply。"""
    pad = int(12 * SCALE)
    fs = int(13 * SCALE)
    font = ImageFont.truetype(FONT_PATH, fs)
    bold = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", fs)
    inner = width - pad * 2
    rows = []
    clipped = False
    for role, text in lines:
        label = {"user": "主人：", "think": "", "reply": "银月："}[role]
        color = {"user": (90, 90, 110), "think": (140, 150, 210), "reply": (60, 60, 120)}[role]
        line, first = label, True
        start = len(rows)
        label_extra = bold.getlength(label) - font.getlength(label)
        for ch in text:
            if ch == "\n" or font.getlength(line + ch) + (label_extra if first else 0) > inner:
                rows.append((line, color, first and bool(label)))
                line, first = "", False
            if ch == "\n":
                continue
            line += ch
        rows.append((line, color, first and bool(label)))
        if role == "user" and len(rows) - start > 3:
            del rows[start + 3:]
            clipped = True
    if len(rows) > 10:
        clipped = True
    if clipped:
        rows = rows[:9] + [(BUBBLE_DETAIL, (74, 84, 170), False)]
    lh = int(fs * 1.55)
    tail_h = int(12 * SCALE)
    hgt = pad * 2 + lh * len(rows) + tail_h
    img = Image.new("RGBA", (width, hgt), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    body = (2, 2, width - 3, hgt - tail_h)
    d.rounded_rectangle((body[0] + 3, body[1] + 4, body[2] + 2, body[3] + 3), radius=int(14 * SCALE), fill=(40, 40, 80, 40))  # 阴影
    d.rounded_rectangle(body, radius=int(14 * SCALE), fill=(255, 255, 255, 245), outline=(170, 178, 230, 255), width=2)
    tx = width - int(70 * SCALE)
    d.polygon((tx, body[3] - 1, tx + int(18 * SCALE), body[3] - 1, tx + int(6 * SCALE), hgt - 2), fill=(255, 255, 255, 245))
    d.line((tx, body[3], tx + int(6 * SCALE), hgt - 2, tx + int(18 * SCALE), body[3]), fill=(170, 178, 230, 255), width=2)
    y = pad
    for text, color, has_label in rows:
        if has_label and text[:3] in ("主人：", "银月："):
            d.text((pad, y), text[:3], font=bold, fill=color)
            d.text((pad + bold.getlength(text[:3]), y), text[3:], font=font, fill=color)
        else:
            d.text((pad, y), text, font=font, fill=color)
        y += lh
    return img


# ======================================================================= 2D / 3D 切换

_a3 = _vrm = None
if _STYLE in ("3d", "vrm"):
    try:
        import avatar3d as _a3  # 用显卡渲染 3D；显卡/驱动不支持时自动退回 2D
        if _STYLE == "vrm":
            import vrm_avatar as _vrm  # 专业动漫模型（VRoid 示例模型改造）
    except Exception:
        _a3 = _vrm = None


def person(p: dict, w=W, h=H) -> Image.Image:
    global _a3, _vrm
    if _vrm:
        try:
            return _vrm.draw_yinyue_vrm(p, OUTFITS, DEFAULT_OUTFIT, w, h)
        except Exception:
            _vrm = None
    if _a3:
        try:
            return _a3.draw_yinyue3d(p, OUTFITS, DEFAULT_OUTFIT, W, H)
        except Exception:
            _a3 = None
    return draw_yinyue(p)


def wolf(phase: float, facing: int = -1) -> Image.Image:
    global _a3
    if _a3:
        try:
            return _a3.draw_wolf3d(phase, facing, W, H)
        except Exception:
            _a3 = None
    return draw_wolf(phase, facing)


# ======================================================================= 分层窗口

class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


# 64 位下句柄是 8 字节，必须声明类型，否则 ctypes 会按 int 截断
user32.GetParent.argtypes, user32.GetParent.restype = [wintypes.HWND], wintypes.HWND
user32.GetDC.argtypes, user32.GetDC.restype = [wintypes.HWND], wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.GetWindowLongPtrW.argtypes, user32.GetWindowLongPtrW.restype = [wintypes.HWND, ctypes.c_int], ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.c_void_p, ctypes.c_void_p, wintypes.HDC,
                                       ctypes.c_void_p, wintypes.COLORREF, ctypes.c_void_p, wintypes.DWORD]
gdi32.CreateCompatibleDC.argtypes, gdi32.CreateCompatibleDC.restype = [wintypes.HDC], wintypes.HDC
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p, wintypes.HANDLE, wintypes.DWORD]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes, gdi32.SelectObject.restype = [wintypes.HDC, wintypes.HGDIOBJ], wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]


class LayeredWindow:
    """一个无边框、置顶、逐像素透明的窗口，内容是一张 RGBA 图片。"""

    def __init__(self, root, master=None):
        self.top = tk.Toplevel(root) if master is None else tk.Toplevel(master)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.geometry("1x1+0+0")
        self.top.update_idletasks()
        self.hwnd = user32.GetParent(self.top.winfo_id())
        ex = user32.GetWindowLongPtrW(self.hwnd, -20)
        user32.SetWindowLongPtrW(self.hwnd, -20, ex | 0x00080000 | 0x00000080)  # WS_EX_LAYERED | WS_EX_TOOLWINDOW（不在任务栏显示）
        self.x = self.y = 0
        self.visible = True

    def show(self, img: Image.Image, x: int, y: int):
        self.x, self.y = x, y
        w, h = img.size
        # 预乘 alpha，并转成 Windows 要求的 BGRA
        px = np.asarray(img, dtype=np.uint16)
        bgra = np.empty((h, w, 4), np.uint8)
        a = px[..., 3]
        bgra[..., 0] = px[..., 2] * a // 255
        bgra[..., 1] = px[..., 1] * a // 255
        bgra[..., 2] = px[..., 0] * a // 255
        bgra[..., 3] = a
        data = bgra.tobytes()
        hdc_screen = user32.GetDC(None)
        hdc = gdi32.CreateCompatibleDC(hdc_screen)
        bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        hbmp = gdi32.CreateDIBSection(hdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        ctypes.memmove(bits, data, len(data))
        old = gdi32.SelectObject(hdc, hbmp)
        blend = BLENDFUNCTION(0, 0, 255, 1)
        drawn = user32.UpdateLayeredWindow(self.hwnd, hdc_screen, ctypes.byref(wintypes.POINT(x, y)),
                                   ctypes.byref(wintypes.SIZE(w, h)), hdc, ctypes.byref(wintypes.POINT(0, 0)),
                                   0, ctypes.byref(blend), 2)
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc)
        user32.ReleaseDC(None, hdc_screen)
        if drawn:
            self.visible = True
        return bool(drawn)

    def hide(self):
        # 不真正隐藏窗口（隐藏过的分层窗口再更新会失败），而是换成一张 1 像素的全透明图
        if self.visible and self.show(HIDDEN_FRAME, self.x, self.y):
            self.visible = False
        return not self.visible


# ======================================================================= 行为

# ---- 原创 K-pop 女团风舞蹈：每拍一个关键姿态，拍间平滑过渡。arms=(左臂, 右臂)，每只=(前后x, 下放z, 弯肘)
KPOP_BPM = 118
_DOWN = (6, 72, 12)
KPOP_STEPS = [
    # 第 1 个八拍：踩点律动 + 叉腰扭胯
    dict(arms=(_DOWN, _DOWN), knee=0.5, hip_sway=0), dict(arms=(_DOWN, _DOWN), knee=0.1, hip_sway=0),
    dict(arms=(_DOWN, _DOWN), knee=0.5, hip_sway=0), dict(arms=(_DOWN, _DOWN), knee=0.1, hip_sway=0),
    dict(arms=((30, 55, 95), (30, 55, 95)), knee=0.3, hip_sway=10), dict(arms=((30, 55, 95), (30, 55, 95)), knee=0.3, hip_sway=-10),
    dict(arms=((30, 55, 95), (30, 55, 95)), knee=0.3, hip_sway=10), dict(arms=((30, 55, 95), (30, 55, 95)), knee=0.1, hip_sway=0, eyes="wink"),
    # 第 2 个八拍：右手指天、左手指天、肩膀 wave
    dict(arms=(_DOWN, (-10, -75, 5)), knee=0.2, hip_sway=8, look=-0.5), dict(arms=(_DOWN, (-10, -75, 5)), knee=0.4, hip_sway=8),
    dict(arms=((-10, -75, 5), _DOWN), knee=0.2, hip_sway=-8, look=0.5), dict(arms=((-10, -75, 5), _DOWN), knee=0.4, hip_sway=-8),
    dict(arms=((0, 10, 40), (0, -10, 0)), knee=0.2, tilt=6), dict(arms=((0, -10, 0), (0, 10, 40)), knee=0.2, tilt=-6),
    dict(arms=((0, 10, 40), (0, -10, 0)), knee=0.2, tilt=6), dict(arms=((0, -10, 0), (0, 10, 40)), knee=0.2, tilt=-6),
    # 第 3 个八拍：头顶比心 + 左右摇
    dict(arms=((-5, -55, 85), (-5, -55, 85)), knee=0.2, hip_sway=6, eyes="happy"), dict(arms=((-5, -55, 85), (-5, -55, 85)), knee=0.3, hip_sway=-6, eyes="happy"),
    dict(arms=((-5, -55, 85), (-5, -55, 85)), knee=0.2, hip_sway=6, eyes="happy"), dict(arms=((-5, -55, 85), (-5, -55, 85)), knee=0.3, hip_sway=-6, eyes="happy"),
    dict(arms=((40, 40, 70), (40, 40, 70)), knee=0.4, hip_sway=0), dict(arms=((40, 40, 70), (40, 40, 70)), knee=0.1, hip_sway=0),
    dict(arms=((40, 40, 70), (40, 40, 70)), knee=0.4, hip_sway=0), dict(arms=((40, 40, 70), (40, 40, 70)), knee=0.1, hip_sway=0, mouth="open"),
    # 第 4 个八拍：展开双臂 → 转圈 → 定格指向主人
    dict(arms=((0, 15, 5), (0, 15, 5)), knee=0.2), dict(arms=((0, 15, 5), (0, 15, 5)), knee=0.3),
    dict(arms=((0, -40, 10), (0, -40, 10)), knee=0.1, spin=180), dict(arms=((0, -40, 10), (0, -40, 10)), knee=0.1, spin=360),
    dict(arms=(_DOWN, _DOWN), knee=0.5), dict(arms=((30, 55, 95), (60, 20, 5)), knee=0.2, hip_sway=8, eyes="wink"),
    dict(arms=((30, 55, 95), (60, 20, 5)), knee=0.2, hip_sway=8, eyes="wink", mouth="open"), dict(arms=((30, 55, 95), (60, 20, 5)), knee=0.2, hip_sway=8, eyes="wink"),
]


def kpop_pose(t: float, bpm: float = KPOP_BPM) -> dict:
    """第 t 秒的舞蹈姿态：找到当前这一拍和下一拍，按拍内进度平滑插值（数字插值，表情按拍切换）。"""
    beat = t * bpm / 60
    i = int(beat) % len(KPOP_STEPS)
    a, b = KPOP_STEPS[i], KPOP_STEPS[(i + 1) % len(KPOP_STEPS)]
    frac = beat - int(beat)
    # 卡点：动作在每拍开头 30% 的时间里利落地打到位（带一点点过冲），剩下的时间定住
    u = min(1.0, frac / 0.3)
    u = 1 - (1 - u) ** 3 + 0.06 * math.sin(math.pi * u)
    lerp = lambda x, y: x + (y - x) * u
    out = {k: v for k, v in a.items() if isinstance(v, str)}
    swing = 14 * math.sin(2 * math.pi * beat)          # 手肘跟着节拍一屈一伸
    out["arms"] = {side: tuple(lerp(x, y) + (sgn * swing if j == 2 else 0)
                               for j, (x, y) in enumerate(zip(a["arms"][k], b["arms"][k])))
                   for k, side, sgn in ((0, "left", 1), (1, "right", -1))}
    out["groove"] = beat                                 # 全身律动层（点头、扭胸、踩步、颠肩、重心移动）
    for key in ("knee", "hip_sway", "tilt", "look"):
        out[key] = lerp(a.get(key, 0.0), b.get(key, 0.0))
    sa, sb = a.get("spin", 0), b.get("spin", 0)
    out["spin"] = lerp(sa, sb if sb >= sa else sa) if sa else (lerp(0, sb) if sb else 0)
    out.setdefault("mouth", "smile")
    out["tail"] = 18 * math.sin(beat * math.pi)
    return out


TRANSFORM_TIME, TRANSFORM_SWAP = 2.1, 0.85   # 换装变身总时长、第几秒换衣服


def draw_magic_ring(t: float, strength: float) -> Image.Image:
    """变身时环绕身体旋转的星光：一圈星星绕着人转，忽大忽小地闪。"""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy, rx, ry = W * 0.5, H * 0.58, W * 0.42, H * 0.12
    for i in range(10):
        a = t * 7 + i * math.tau / 10
        x, y = cx + rx * math.cos(a), cy + ry * math.sin(a) - H * 0.25 * ((i % 3) / 3) * min(1, t * 2)
        r = (3 + 2.5 * math.sin(t * 20 + i)) * SCALE
        col = (255, 225, 140, int(230 * strength)) if i % 2 else (210, 220, 255, int(230 * strength))
        d.polygon((x, y - 2 * r, x + r * 0.45, y - r * 0.45, x + 2 * r, y, x + r * 0.45, y + r * 0.45,
                   x, y + 2 * r, x - r * 0.45, y + r * 0.45, x - 2 * r, y, x - r * 0.45, y - r * 0.45), fill=col)
    return img


# 主人用嘴点名的动作和时长（秒）；run（跑一圈）和 sleep（睡觉）单独处理
FORCED_ACTIONS = {"kpop": len(KPOP_STEPS) * 60 / KPOP_BPM, "spin": 1.2, "hop": 1.6, "wave": 2.2, "dance": 5.0, "heart": 3.0, "wink": 1.6, "nod": 2.0,
                  "yawn": 2.6, "sing": 5.0, "blush": 2.5, "stretch": 2.4, "tail": 2.5, "look_around": 3.5}
if _vrm:
    FORCED_ACTIONS["dance"] = 12 * 60 / KPOP_BPM
    FORCED_ACTIONS["kpop"] = (4 * len(dance_choreo.PHRASES) + (8 if _vrm.DANCE_DURATION else 0)) * 60 / KPOP_BPM

# 待机小动作和各自时长（秒）。晚上更容易打哈欠
IDLE_ACTIONS = {"tail": 2.5, "ear": 1.2, "tilt": 2.5, "wave": 2.2, "stretch": 2.4, "look": 3.0, "hop": 1.6,
                "wink": 1.4, "look_around": 3.5, "sway": 4.0, "sing": 5.0, "heart": 3.0, "nod": 2.0,
                "yawn": 2.6, "blush": 2.5}
VRM_IDLE_ACTIONS = {"look_around": 3.5, "stretch": 3.2, "tail": 2.8, "glance_back": 3.1, "yawn": 2.6}


class MonitorInfo(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT), ("work", wintypes.RECT), ("flags", wintypes.DWORD)]


def work_area_at(x, y, fallback):
    user32.MonitorFromPoint.argtypes = (wintypes.POINT, wintypes.DWORD)
    user32.MonitorFromPoint.restype = wintypes.HANDLE
    monitor = user32.MonitorFromPoint(wintypes.POINT(x, y), 2)
    info = MonitorInfo(ctypes.sizeof(MonitorInfo))
    if monitor and user32.GetMonitorInfoW(ctypes.c_void_p(monitor), ctypes.byref(info)):
        return info.work.left, info.work.top, info.work.right, info.work.bottom
    return fallback


def ease(t):
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def clip_to_work_area(image, x, y, area):
    """Use the screen boundary to occlude the character, including on a shared monitor edge."""
    left, top, right, bottom = area
    crop = (max(0, left - x), max(0, top - y), min(image.width, right - x), min(image.height, bottom - y))
    if crop[0] >= crop[2] or crop[1] >= crop[3]:
        return HIDDEN_FRAME, x, y
    return image.crop(crop), x + crop[0], y + crop[1]


def walk_cycle(progress, distance, other_foot=False):
    """Two steps and a closing step. Coordinates are along the direction of travel."""
    u = max(0.0, min(progress, 1.0))
    travel = ease(u)
    if u < 0.40:
        swing = u / 0.40
        feet = (0.54 * ease(swing), 0.0)
        lift = (0.061 * math.sin(math.pi * swing), 0.0)
    elif u < 0.80:
        swing = (u - 0.40) / 0.40
        feet = (0.54, ease(swing))
        lift = (0.0, 0.061 * math.sin(math.pi * swing))
    else:
        swing = (u - 0.80) / 0.20
        feet = (0.54 + 0.46 * ease(swing), 1.0)
        lift = (0.036 * math.sin(math.pi * swing), 0.0)
    ground = min(1.0, u / 0.13, (1.0 - u) / 0.13)
    planted = tuple(((foot - travel) * distance, height) for foot, height in zip(feet, lift))
    # Lower the pelvis only enough for the longest reach in this frame.
    leg_length = 0.766
    extent = max(abs(reach - 0.0275) for reach, _ in planted)
    required_drop = leg_length - math.sqrt(max(0.0, leg_length**2 - extent**2))
    arms = (1 if not other_foot else -1) * math.sin(2.5 * math.pi * u) * ground
    return travel, {
        "walk_feet": planted[::-1] if other_foot else planted,
        "walk_drop": max(0.011 * ground, required_drop + 0.003 * ground) if ground else 0.0,
        "arms": {"left": (-11 * arms, 77, 10), "right": (11 * arms, 77, 10)},
        "bob": 0.0,
    }


class Avatar:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.body = LayeredWindow(self.root)
        self.bubble = LayeredWindow(self.root)
        self.bubble.hide()
        self.hidden = False
        self.show_ack = []
        self.state = "idle"            # idle / listening / thinking / speaking / sleeping
        self.action, self.action_t0 = None, 0.0
        self.next_action = time.time() + random.uniform(12, 30)
        self.last_idle_action = None
        self.next_blink = time.time() + random.uniform(2, 5)
        self.last_active = time.time()
        self.lines: list[tuple[str, str]] = []
        self.last_dialogue: list[tuple[str, str]] = []
        self.rest_before_wake = None
        self.rest_before_turn = "awake"
        self.turn = None
        self.bubble_content = ()
        self.bubble_image = None
        self.dialogue_window = None
        try:
            self.outfit = json.loads(SAVE_FILE.read_text(encoding="utf-8")).get("outfit", DEFAULT_OUTFIT)
        except Exception:
            self.outfit = DEFAULT_OUTFIT
        if self.outfit not in OUTFITS:
            self.outfit = DEFAULT_OUTFIT
        self.outfit_changed_at = 0.0
        self.prev_outfit = self.outfit
        self.bubble_hide_at = 0.0
        self.phase = 0.0

        wa = wintypes.RECT()
        user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa), 0)  # SPI_GETWORKAREA：去掉任务栏后的区域
        self.wa = (wa.left, wa.top, wa.right, wa.bottom)
        import music_sync
        self.music = music_sync.Player()
        self.dance = {"bpm": KPOP_BPM, "offset": 0.0, "dur": FORCED_ACTIONS["kpop"]}
        self.dance_order = []
        self.last_dance_opener = None
        self.lap = None              # 正在跑圈：{"t0", "path"}
        self.lap_ack = None           # 等待本轮跑圈结束的本机工具
        self.dance_ack = None         # 等待首帧实际显示后才确认开始
        self.forced = None           # 主人点名的动作：(名字, 开始时间)
        self.x = wa.right - W - int(12 * SCALE)
        self.y = wa.bottom - H
        self._drag = None
        self._edge_hide = None
        self._farewell = None
        self._last_farewell = None
        self.walk_started = None
        self.walk_at = time.time() + 8
        for w in (self.body.top,):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)
            w.bind("<Button-3>", self._menu)
        self.bubble.top.bind("<Button-1>", lambda _: self._show_dialogue())

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", PORT))
        self.sock.setblocking(False)

    # ---------------- 鼠标
    def _drag_start(self, e):
        if self._farewell:
            return
        self._edge_hide = None
        self.walk_started = None
        self.walk_at = time.time() + 12
        if not self.forced or self.forced[0] not in ("dance", "kpop"):
            self.x, self.y = self.body.x, self.body.y
        self._drag = (e.x_root - self.x, e.y_root - self.y, e.x_root, e.y_root)

    def _drag_move(self, e):
        if not self._drag:
            return
        self.wa = work_area_at(e.x_root, e.y_root, self.wa)
        left, top, right, bottom = self.wa
        self.x = max(left, min(right - W, e.x_root - self._drag[0]))
        self.y = max(top, min(bottom - H, e.y_root - self._drag[1]))

    def _drag_end(self, e):
        drag, self._drag = self._drag, None
        if not drag or math.hypot(e.x_root - drag[2], e.y_root - drag[3]) < 8 * SCALE:
            return
        left, top, right, bottom = self.wa
        distances = {"left": e.x_root - left, "top": e.y_root - top,
                     "right": right - 1 - e.x_root, "bottom": bottom - 1 - e.y_root}
        edge = "bottom" if distances["bottom"] <= 24 * SCALE else min(distances, key=distances.get)
        if distances[edge] > 24 * SCALE:
            return
        self._on_event("action", "stop")
        self.action = None
        self._hide_bubble()
        side_pose = person({"outfit": self.outfit, "spin": EDGE_TURNS[edge]})
        x0, y0, x1, y1 = side_pose.getbbox() or (0, 0, W, H)
        # Move until the silhouette clears the boundary. The transparent window margins do not count.
        target = {
            "left": (left - x1 - 3, self.y),
            "right": (right - x0 + 3, self.y),
            "top": (self.x, top - y1 - 3),
            "bottom": (self.x, bottom - y0 + 3),
        }[edge]
        peek_pose = self._edge_peek_pose(edge, 1.0)
        measure = person({**peek_pose, "head_only": True})
        x0, y0, x1, y1 = measure.getbbox() or (0, 0, W, H // 3)
        # The face and ears need to be visible, while the ends of the hair can stay by the edge.
        face_bottom = y0 + round((y1 - y0) * 0.58)
        face = measure.crop((0, 0, W, face_bottom)).getbbox() or (x0, y0, x1, y1)
        if edge in ("left", "right"):
            pose_frame = person(peek_pose)
            waist_y = min(H - 1, face[3] + round(H * 0.14))
            lower_bounds = pose_frame.crop((0, waist_y, W, H)).getbbox()
            if lower_bounds:
                x0, y0, x1, y1 = face
                if edge == "left":
                    x0 = max(x0, lower_bounds[2] + 2)
                else:
                    x1 = min(x1, lower_bounds[0] - 2)
                face = (x0, y0, x1, y1)
        self._edge_hide = (time.monotonic(), edge, target, face)

    def _edge_peek_pose(self, edge, showing):
        inward = {"left": 1, "right": -1}.get(edge, 0)
        lean = inward * ease(showing * 1.27)
        return {"outfit": self.outfit, "spin": -15 - inward * (13 + 10 * (1 - showing)), "peek": lean,
                "peek_bend": showing if edge == "bottom" else 0.0,
                "tail": 86 if edge == "left" else 0, "look": 0.52 * inward, "mouth": "smile"}

    def _retreat_frame(self, edge, target, head_bounds, elapsed):
        left, top, right, bottom = self.wa
        if elapsed < EDGE_STEP_END:
            dx, dy = target[0] - self.x, target[1] - self.y
            stride = min(0.72, math.hypot(dx, dy) * 2.03 / H)
            travel, pose = walk_cycle((elapsed - EDGE_TURN_SECONDS) / (EDGE_STEP_END - EDGE_TURN_SECONDS),
                                      stride, edge == "left")
            pose.update(outfit=self.outfit, spin=EDGE_TURNS[edge] * ease(elapsed / EDGE_TURN_SECONDS))
            x = round(self.x + dx * travel)
            y = round(self.y + dy * travel)
            return clip_to_work_area(person(pose), x, y, self.wa)
        if edge == "top":
            return HIDDEN_FRAME, self.x, top

        showing = ease((elapsed - EDGE_STEP_END) / (EDGE_PEEK_END - EDGE_STEP_END))
        showing *= 1 - ease((elapsed - EDGE_PEEK_HOLD_END) / (EDGE_WITHDRAW_SECONDS - EDGE_PEEK_HOLD_END))
        pose = self._edge_peek_pose(edge, showing)
        if 2.45 < elapsed < 2.55:
            pose["eyes"] = "closed"
        body = person(pose)
        x0, _, x1, y1 = head_bounds
        x, y = self.x, self.y
        if edge == "left":
            x = round(left - W + (W - x0 + 2) * showing)
        elif edge == "right":
            x = round(right - (x1 + 2) * showing)
        else:
            y = round(bottom - (y1 + H * 0.08) * showing)
        return clip_to_work_area(body, x, y, self.wa)

    def _menu(self, e):
        m = tk.Menu(self.root, tearoff=0)
        sub = tk.Menu(m, tearoff=0)
        for name in OUTFITS:
            sub.add_command(label=("✓ " if name == self.outfit else "   ") + name, command=lambda n=name: self.set_outfit(n))
        m.add_cascade(label="换装", menu=sub)
        m.add_command(label="查看完整对话", command=self._show_dialogue,
                      state="normal" if self.last_dialogue else "disabled")
        m.add_command(label="收起对话框", command=lambda: self._hide_bubble())
        m.add_command(label="隐藏形象", command=self._hide_avatar)
        m.add_command(label="退出形象", command=self.root.destroy)
        m.tk_popup(e.x_root, e.y_root)

    # ---------------- 事件
    def _poll(self):
        while True:
            try:
                payload, sender = self.sock.recvfrom(65536)
                msg = json.loads(payload.decode("utf-8"))
            except BlockingIOError:
                break
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue
            kind, text = msg.get("type"), msg.get("text", "")
            if kind == "rest_status":
                try:
                    state = self.rest_before_turn if msg.get("turn") == self.turn else "unknown"
                    self.sock.sendto(state.encode("ascii"), sender)
                except OSError:
                    pass
                continue
            if kind == "action" and text == "show":
                was_visible = not self.hidden and not self._farewell and self._edge_hide is None and self.body.visible
                self._farewell = None
                self._edge_hide = None
                self.hidden = False
                if msg.get("ack"):
                    if was_visible:
                        self._ack(sender, b"ok")
                    else:
                        self.show_ack.append(sender)
                continue
            if kind == "outfit" and msg.get("ack"):
                try:
                    worn = self.set_outfit(text)
                except OSError:
                    worn = None
                self._ack(sender, worn.encode("utf-8") if worn else b"failed")
                continue
            if kind == "action" and text == "hide":
                outcome = self._start_farewell()
                if msg.get("ack"):
                    self._ack(sender, outcome)
                continue
            if (self.hidden or self._farewell) and kind == "action" and text in ("dance", "kpop", "run"):
                if msg.get("ack"):
                    self._ack(sender, b"hidden")
                continue
            self._on_event(kind, text, msg.get("turn"))
            if kind == "action" and text == "run" and msg.get("ack"):
                try:
                    self.sock.sendto(b"ok", sender)
                    self.lap_ack = sender
                except OSError:
                    pass
            if kind == "action" and text in ("dance", "kpop") and msg.get("ack"):
                self.dance_ack = (text, sender)

    def _ack(self, sender, payload):
        try:
            self.sock.sendto(payload, sender)
        except OSError:
            pass

    def _hide_avatar(self):
        self._farewell = None
        self._edge_hide = None
        self.walk_started = None
        self.walk_at = time.time() + 12
        if self.dialogue_window and self.dialogue_window.winfo_exists():
            self.dialogue_window.destroy()
        body_hidden = self.body.hide()
        bubble_hidden = self.bubble.hide()
        if not (body_hidden and bubble_hidden):
            return False
        self.hidden = True
        self.last_frame = HIDDEN_FRAME
        self.lines = []
        self.forced = self.action = self.lap = None
        self.music.stop()
        self._finish_run(b"interrupted")
        if self.dance_ack:
            self._ack(self.dance_ack[1], b"hidden")
            self.dance_ack = None
        for sender in self.show_ack:
            self._ack(sender, b"interrupted")
        self.show_ack.clear()
        return True

    def _start_farewell(self):
        if self.hidden:
            return b"ok"
        if self._farewell or self._edge_hide:
            return b"leaving"
        self._drag = None
        self.walk_started = None
        self.action = None
        self._on_event("action", "stop")
        if self.dance_ack:
            self._ack(self.dance_ack[1], b"interrupted")
            self.dance_ack = None
        if self.dialogue_window and self.dialogue_window.winfo_exists():
            self.dialogue_window.destroy()
        self._hide_bubble()
        choice = random.choice([name for name in FAREWELL_TIMES if name != self._last_farewell])
        self._last_farewell = choice
        self._farewell = (choice, time.monotonic())
        return b"leaving"

    def _farewell_frame(self, choice, t):
        pose = {"outfit": self.outfit, "eyes": "happy", "mouth": "smile"}
        if choice == "wolf":
            left, _, right, _ = self.wa
            facing = -1 if self.x - left < right - self.x - W else 1
            if t < 0.47:
                frame = person(pose).copy() if t < 0.24 else wolf(0, facing).copy()
                frame.alpha_composite(draw_puff(t / 0.47))
                return frame, self.x, self.y
            run = (t - 0.47) / (FAREWELL_TIMES["wolf"] - 0.47)
            end_x = left - W if facing < 0 else right
            travel = 0.25 * run + 0.75 * run * run
            x = round(self.x + (end_x - self.x) * travel)
            return clip_to_work_area(wolf((t - 0.47) * 19, facing), x, self.y, self.wa)

        fade = ease((t - 0.83) / (FAREWELL_TIMES[choice] - 0.83))
        if choice == "wave":
            arm = ease(t / 0.33) * (1 - ease((t - 0.92) / 0.42))
            wave = 16 * math.sin(t * 17) * arm
            pose.update(wave_raise=arm, wave=wave, tilt=5 * arm, tail=8 * math.sin(t * 6))
            if not _vrm and arm > 0.45:
                pose["arm"] = "wave"
        elif choice == "bow":
            bow = ease(t / 0.48) * (1 - ease((t - 0.64) / 0.54))
            pose.update(bow=bow, eyes="closed" if bow > 0.4 else "open", tilt=4 * bow)
        else:
            turn = ease(t / 1.55)
            pose.update(spin=360 * turn, jump=15 * fade, tail=15 * math.sin(t * 7))

        frame = person(pose).copy()
        if choice == "moon" and fade:
            small = frame.resize((round(W * (1 - 0.62 * fade)), round(H * (1 - 0.62 * fade))), Image.LANCZOS)
            frame = Image.new("RGBA", (W, H))
            frame.alpha_composite(small, ((W - small.width) // 2, round((H - small.height) * 0.60)))
        frame.putalpha(frame.getchannel("A").point(lambda a: round(a * (1 - fade))))
        if choice in ("wave", "moon"):
            light = ease((t - 0.3) / 0.55) * (1 - fade)
            frame.alpha_composite(draw_magic_ring(t, light * (0.7 if choice == "moon" else 0.36)))
        if choice == "moon" and fade:
            moon = Image.new("RGBA", (W, H))
            draw = ImageDraw.Draw(moon)
            radius = W * (0.078 + 0.035 * fade)
            x, y = W * 0.50, H * (0.56 - 0.08 * fade)
            alpha = round(230 * math.sin(math.pi * fade))
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(233, 239, 255, alpha))
            draw.ellipse((x - radius * 0.38, y - radius * 1.08, x + radius * 1.10, y + radius * 0.45),
                         fill=(0, 0, 0, 0))
            frame.alpha_composite(moon)
        return frame, self.x, self.y

    def start_dance(self):
        """开始跳舞：music 文件夹里有主人的歌就挑一首播放、按歌的节拍跳完整首；没有就按默认节奏跳一段。"""
        self.dance = {"bpm": KPOP_BPM, "offset": 0.0, "dur": FORCED_ACTIONS["kpop"]}
        self._prepare_dance_order(720, recorded=bool(_vrm and _vrm.DANCE_DURATION))
        self.forced = ("kpop", time.time())
        action = self.forced

        def work():
            try:
                info = self.music.start(lambda: self.forced is action)
            except Exception:
                info = None
            if info and self.forced is action:
                bpm, offset, length, title = info
                self.dance = {"bpm": bpm, "offset": offset, "dur": min(length, 240)}
                self.forced = ("kpop", time.time())   # 从音乐开始的那一刻重新计时，对齐节拍
        threading.Thread(target=work, daemon=True).start()

    def _prepare_dance_order(self, beats, recorded=False):
        self.dance_order = dance_choreo.program(beats, recorded, previous=self.last_dance_opener)
        self.last_dance_opener = self.dance_order[0][2]

    def start_run(self):
        """跑一圈：右下角 → 沿底边跑到左下角 → 斜着跑到屏幕中央 → 回到右下角。"""
        self._finish_run(b"interrupted")
        left, top, right, bottom = self.wa
        home = (self.x, self.y)
        path = [home, (left + int(10 * SCALE), home[1]),
                ((left + right) // 2 - W // 2, (top + bottom) // 2 - H // 2), home]
        self.lap = {"t0": time.time(), "path": path, "home": home}

    def _finish_run(self, status):
        if self.lap_ack is not None:
            try:
                self.sock.sendto(status, self.lap_ack)
            except OSError:
                pass
            self.lap_ack = None

    def _run_frame(self, now):
        """跑圈的这一帧：返回 (图, x, y)，跑完返回 None。"""
        r = self.lap
        t = now - r["t0"]
        transform = 0.5                                  # 变身烟雾持续时间
        speed = 1100 * SCALE / 1.5                       # 像素/秒
        segs = list(zip(r["path"], r["path"][1:]))
        lens = [math.dist(a, b) for a, b in segs]
        total = sum(lens) / speed
        if t < transform:                                # 起跑前：人形消失在烟雾里
            img = person({"outfit": self.outfit, "eyes": "happy", "mouth": "open"}) if t < transform / 2 \
                else wolf(0, -1)
            img.alpha_composite(draw_puff(t / transform))
            return img, *r["home"]
        t -= transform
        if t > total:                                    # 跑完：变回人形
            t2 = t - total
            if t2 > transform:
                return None
            img = wolf(0, 1) if t2 < transform / 2 else person({"outfit": self.outfit, "eyes": "happy"})
            img.alpha_composite(draw_puff(t2 / transform))
            return img, *r["home"]
        dist = t * speed
        for (a, b), ln in zip(segs, lens):
            if dist <= ln:
                u = dist / ln if ln else 1
                x, y = a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u
                facing = -1 if b[0] < a[0] else 1
                return wolf(t * 16, facing), int(x), int(y)
            dist -= ln
        return None

    def _on_event(self, kind, text, turn=None):
        now = time.time()
        if kind == "emote":
            if self.lap is None and self.forced is None:
                self.forced = (text, now)
            return
        if kind == "action":
            if text == "run":
                self.start_run()
            elif text == "sleep":
                self.state, self.last_active = "sleeping", now - 3601
            elif text in ("stop", "stop_dance"):   # 停下：结束跳舞和音乐
                self.forced = None
                self.music.stop()
                self.lap = None
                self._finish_run(b"interrupted")
            elif text in ("dance", "kpop"):
                self.lap = None
                self._finish_run(b"interrupted")
                if text == "kpop":
                    self.start_dance()
                else:
                    self._prepare_dance_order(12)
                    self.forced = (text, now)
            else:
                self.forced = (text, now)
            return
        if kind == "outfit":
            self.set_outfit(text)
            return
        if kind in ("thinking", "tool", "reply", "speak_end", "end") and turn is not None and turn != self.turn:
            return
        self.last_active = now
        if kind == "wake":
            if self.state != "listening":
                self.rest_before_wake = "sleeping" if self.state == "sleeping" else "awake"
            self.turn = None
            self.state, self.lines = "listening", [("think", "我在听……")]
            self.action = None
        elif kind == "user":
            self.rest_before_turn = self.rest_before_wake or ("sleeping" if self.state == "sleeping" else "awake")
            self.rest_before_wake = None
            self.turn = turn
            self.lines = [("user", text)]
            self.last_dialogue = self.lines.copy()
            self.state = "listening"
        elif kind == "thinking":
            self.lines = [l for l in self.lines if l[0] != "think"] + [("think", "思考中……")]
            self.state = "thinking"
        elif kind == "tool":
            self.lines = [l for l in self.lines if l[0] != "think"] + [("think", text)]
        elif kind == "reply":
            self.lines = [l for l in self.lines if l[0] == "user"] + [("reply", text)]
            self.last_dialogue = self.lines.copy()
            if self.dialogue_window and self.dialogue_window.winfo_exists():
                self._show_dialogue()
            self.state = "speaking"
        elif kind == "speak_end":
            self.state = "listening"
            self.bubble_hide_at = now + 15
            return
        elif kind == "end":
            self.state = "idle"
            self.turn = None
            self.rest_before_wake = None
            if not self.bubble_hide_at:
                self.bubble_hide_at = now + 1.5
            return
        self.bubble_hide_at = 0

    def set_outfit(self, name):
        names = list(OUTFITS)
        if name in ("next", "换一套", ""):
            name = names[(names.index(self.outfit) + 1) % len(names)]
        elif name in ("random", "随机"):
            name = random.choice([n for n in names if n != self.outfit])
        if name not in OUTFITS:
            return
        self.prev_outfit = self.outfit
        SAVE_FILE.parent.mkdir(exist_ok=True)
        SAVE_FILE.write_text(json.dumps({"outfit": name}, ensure_ascii=False), encoding="utf-8")
        self.outfit, self.outfit_changed_at = name, time.time()
        return name

    def _hide_bubble(self):
        self.bubble.hide()
        self.lines = []

    def _show_dialogue(self):
        if not self.last_dialogue:
            return
        if not self.dialogue_window or not self.dialogue_window.winfo_exists():
            window = self.dialogue_window = tk.Toplevel(self.root)
            window.title("银月对话")
            window.attributes("-topmost", True)
            left, top, right, bottom = self.wa
            width = min(int(440 * SCALE), right - left)
            height = min(int(380 * SCALE), bottom - top)
            x = max(left, min(self.x + W - width, right - width))
            y = max(top, min(self.y - height - 12, bottom - height))
            window.geometry(f"{width}x{height}{x:+d}{y:+d}")
            scroll = tk.Scrollbar(window)
            scroll.pack(side="right", fill="y")
            self.dialogue_text = tk.Text(window, wrap="char", padx=16, pady=14,
                                         font=("Microsoft YaHei UI", 11), yscrollcommand=scroll.set)
            self.dialogue_text.pack(fill="both", expand=True)
            scroll.configure(command=self.dialogue_text.yview)
        view = self.dialogue_text
        view.configure(state="normal")
        view.delete("1.0", "end")
        labels = {"user": "主人", "reply": "银月"}
        view.insert("1.0", "\n\n".join(f"{labels[role]}\n{text}" for role, text in self.last_dialogue))
        view.configure(state="disabled")
        view.yview_moveto(0)
        self.dialogue_window.lift()

    # ---------------- 每一帧
    def _pose(self, now) -> dict:
        p = {"bob": 1.5 * math.sin(now * 2.2), "tail": 6 * math.sin(now * 1.3), "phase": self.phase, "outfit": self.outfit}
        if self.forced:  # 主人点名的动作优先
            name, t0 = self.forced
            dur = self.dance["dur"] if name == "kpop" else FORCED_ACTIONS.get(name, 2.5)
            if now - t0 < dur:
                self._apply_action(p, name, now - t0, dur)
                return p
            self.forced = None
        t = now - self.outfit_changed_at
        if t < TRANSFORM_TIME:  # 换装变身：举手转圈 → 光雾炸开时换衣服 → 穿着新衣服挥手亮相
            if t < TRANSFORM_SWAP:
                p.update(outfit=self.prev_outfit, arm="up", eyes="closed", mouth="smile",
                         spin=720 * (t / TRANSFORM_SWAP) ** 1.6, jump=3 * math.sin(t / TRANSFORM_SWAP * math.pi))
            else:
                u = (t - TRANSFORM_SWAP) / (TRANSFORM_TIME - TRANSFORM_SWAP)
                p.update(arm="wave", wave=22 * math.sin(u * 12), eyes="happy", mouth="open",
                         tail=20 * math.sin(u * 14), extra="sparkle", jump=4 * max(0.0, math.sin(u * math.pi * 2)))
            return p
        if now >= self.next_blink:
            p["eyes"] = "closed"
            if now >= self.next_blink + 0.14:
                self.next_blink = now + random.uniform(2.5, 6)

        if self.state == "sleeping":
            p.update(eyes="sleep", mouth="flat", tilt=10, bob=2.5 * math.sin(now * 1.2), tail=2 * math.sin(now * 0.8),
                     extra="zzz")
            return p
        if self.state == "listening":
            p.update(ear=4 * math.sin(now * 8) * (1 if int(now * 2) % 3 == 0 else 0), extra="sparkle",
                     tilt=5, look=-0.5)
            return p
        if self.state == "thinking":
            p.update(tilt=6, mouth="o", extra="think", eyes=p.get("eyes", "open"))
            return p
        if self.state == "speaking":
            p.update(mouth="open" if int(now * 7) % 2 else "smile", tail=12 * math.sin(now * 3),
                     tilt=3 * math.sin(now * 1.7))
            if int(now / 3) % 3 == 0:  # 每隔几秒抬手比划一下
                p.update(arm="wave", wave=12 * math.sin(now * 5))
            return p

        if _vrm and self._drag is None and not self.lines and self.action is None and now >= self.walk_at:
            if self.walk_started is None:
                self.walk_started = now
                self.action = None
            t = now - self.walk_started
            if t < 4.12:
                left, _, right, _ = self.wa
                direction = -1 if self.x - left > right - self.x - W else 1
                distance = min(W * 0.38, max(0, self.x - left if direction < 0 else right - self.x - W))
                side = -105 if direction < 0 else 75
                reverse = 75 if direction < 0 else -105
                if t < 1.78:
                    travel, gait = walk_cycle((t - 0.35) / 1.43, distance * 2.03 / H)
                    turn = side * ease(t / 0.35)
                elif t < 2.30:
                    travel = 1.0
                    turn = side + (reverse - side) * ease((t - 1.78) / 0.52)
                    gait = {}
                else:
                    progress, gait = walk_cycle((t - 2.30) / 1.43, distance * 2.03 / H, other_foot=True)
                    travel = 1.0 - progress
                    turn = reverse * (1.0 - ease((t - 3.73) / 0.39))
                p.update(gait, spin=turn, window_dx=round(direction * distance * travel))
                return p
            self.walk_started = None
            self.walk_at = now + random.uniform(40, 64)

        # idle：偶尔做个小动作
        idle_actions = VRM_IDLE_ACTIONS if _vrm else IDLE_ACTIONS
        if self.action is None and now >= self.next_action:
            names = [name for name in idle_actions if name != self.last_idle_action]
            weights = [3 if (n == "yawn" and (time.localtime().tm_hour >= 23 or time.localtime().tm_hour < 6)) else 1
                       for n in names]
            self.action, self.action_t0 = random.choices(names, weights)[0], now
            self.last_idle_action = self.action
        if self.action:
            t = now - self.action_t0
            dur = idle_actions.get(self.action, VRM_IDLE_ACTIONS.get(self.action, 2.5))
            if t > dur:
                self.action = None
                self.next_action = now + random.uniform(9, 23)
            else:
                self._apply_action(p, self.action, t, dur)
        # 一个小时没人理就睡觉
        if now - self.last_active > 3600:
            self.state = "sleeping"
        return p

    def _apply_action(self, p, name, t, dur):
        if name == "tail":
            strength = math.sin(math.pi * min(1.0, t / dur))
            p.update(tail=18 * strength * math.sin(t * 6), spin=21 * strength, look=0.5 * strength)
        elif name == "ear":
            p["ear"] = 6 * math.sin(t * 20)
        elif name == "tilt":
            p.update(tilt=10 * math.sin(t / dur * math.pi), eyes="happy" if 0.5 < t < 2 else p.get("eyes", "open"))
        elif name == "wave":
            p.update(arm="wave", wave=25 * math.sin(t * 9), eyes="happy", mouth="open" if t < 1 else "smile")
        elif name == "stretch":
            reach = ease(t / 0.8) * (1 - ease((t - dur * 0.65) / (dur * 0.35)))
            p.update(arms={"left": (-16 * reach, 72 - 132 * reach, 15), "right": (-12 * reach, 72 - 127 * reach, 15)},
                     reach=reach, eyes="closed" if reach > 0.8 else "open", mouth="o" if reach > 0.9 else "smile")
            if not _vrm and reach > 0.5:
                p["arm"] = "up"
        elif name == "look":
            p.update(tilt=-8 * math.sin(t / dur * math.pi), extra="note")
        elif name == "hop":                       # 原地蹦两下
            p.update(jump=6 * abs(math.sin(t / dur * 2 * math.pi)), eyes="happy", mouth="open",
                     tail=15 * math.sin(t * 10))
        elif name == "wink":                      # 俏皮地眨一只眼
            p.update(eyes="wink", mouth="smile", tilt=5, blush=170)
        elif name == "look_around":               # 左看看右看看
            lk = -math.sin(2 * math.pi * t / dur)
            p.update(look=lk, spin=19 * lk, tilt=-4 * lk, ear=2 * lk)
        elif name == "glance_back":
            turn = ease(t / 0.8) * (1 - ease((t - 1.8) / (dur - 1.8)))
            p.update(spin=52 * turn, look=-0.75 * turn, tilt=-3 * turn, tail=17 * turn)
        elif name == "sway":                      # 身子轻轻左右晃
            p.update(dx=3 * math.sin(t * 3), tilt=4 * math.sin(t * 3), tail=12 * math.sin(t * 3 + 1))
        elif name == "sing":                      # 哼个小曲
            p.update(mouth="o" if int(t * 3) % 2 else "smile", eyes="happy", extra="note",
                     dx=2 * math.sin(t * 2.5), tilt=6 * math.sin(t * 2.5))
        elif name == "heart":                     # 冒个小爱心
            p.update(eyes="happy", blush=180, extra="heart", mouth="smile")
        elif name == "nod":                       # 点点头
            p.update(tilt=3 * math.sin(t * 9), bob=p["bob"] + 1.5 * abs(math.sin(t * 9)))
        elif name == "yawn":                      # 打个哈欠
            p.update(eyes="closed", mouth="yawn" if 0.4 < t < 2.0 else "flat", arm="up" if t < 1.6 else "down",
                     tilt=4)
        elif name == "blush":                     # 害羞
            p.update(eyes="happy", blush=210, look=-1, tilt=-6, mouth="smile")
        elif name in ("kpop", "dance"):
            offset = self.dance["offset"] if name == "kpop" else 0.0
            bpm = self.dance["bpm"] if name == "kpop" else KPOP_BPM
            elapsed = max(0.0, t - offset)
            if _vrm and self.dance_order:
                strength = ease(max(0.0, dur - t) / 0.42)
                p.update(dance_choreo.pose(self.dance_order, elapsed * bpm / 60, _vrm.DANCE_DURATION, strength))
                p["bob"] = 0
            else:
                p.update(kpop_pose(elapsed, bpm) if t >= offset else {"knee": 0.15})
        elif name == "spin":                             # 转一圈
            p.update(spin=360 * min(1.0, t / dur), eyes="happy", mouth="open")

    def tick(self):
        self.phase = (self.phase + 0.08) % 100
        self._poll()
        now = time.time()
        if self.hidden:
            self.lines = []
            self.root.after(80, self.tick)
            return
        if self._farewell:
            choice, started = self._farewell
            elapsed = time.monotonic() - started
            if elapsed >= FAREWELL_TIMES[choice]:
                self._hide_avatar()
            else:
                frame, x, y = self._farewell_frame(choice, elapsed)
                self.body.show(frame, x, y)
                self.last_frame = frame
            self.root.after(32, self.tick)
            return
        if self._edge_hide:
            started, edge, target, head_bounds = self._edge_hide
            elapsed = time.monotonic() - started
            if elapsed >= EDGE_WITHDRAW_SECONDS:
                self._hide_avatar()
            else:
                frame, x, y = self._retreat_frame(edge, target, head_bounds, elapsed)
                self.body.show(frame, x, y)
                self.last_frame = frame
            self.root.after(25, self.tick)
            return
        if self.state == "sleeping" and now - self.last_active < 5:
            self.state = "listening"
        if self.lap:
            frame = self._run_frame(now)
            if frame:
                drawn = self.body.show(*frame)
                self._confirm_show(drawn)
                self.last_frame = frame[0]
                self.bubble.hide()
                self.root.after(40, self.tick)  # 跑的时候刷新快一点，动作更顺
                return
            self.lap = None
            self._finish_run(b"done")
        pose = self._pose(now)
        dancing = self.forced and self.forced[0] in ("dance", "kpop")
        if dancing and _vrm:
            pose["viewport_height"] = H
            img = person(pose, W + 2 * DANCE_PAD_X, H + 2 * DANCE_PAD_Y)
        else:
            img = person(pose)
        pad_x, pad_y = (img.width - W) // 2, (img.height - H) // 2
        t = now - self.outfit_changed_at
        if t < TRANSFORM_TIME:  # 变身特效叠在人物上：旋转的星光圈 + 换衣服那一刻炸开的光雾
            img = img.copy()
            if t < TRANSFORM_SWAP + 0.4:
                img.alpha_composite(draw_magic_ring(t, min(1.0, t / 0.3)), (pad_x, pad_y))
            if TRANSFORM_SWAP - 0.3 < t < TRANSFORM_SWAP + 0.35:
                img.alpha_composite(draw_puff((t - (TRANSFORM_SWAP - 0.3)) / 0.65), (pad_x, pad_y))
        x, y = self.x + pose.get("window_dx", 0) - pad_x, self.y - pad_y
        self.last_frame = img  # 给手机网页看的实时画面
        if (pad_x or pad_y) and (bounds := img.getbbox()):
            left, top, right, bottom = self.wa
            x = max(left - bounds[0], min(right - bounds[2], x))
            y = max(top - bounds[1], min(bottom - bounds[3], y))
            # The phone preview fits height, so remove spare padding without resizing pixels.
            preview_top = max(bounds[3] - H, min(bounds[1], pad_y))
            preview_bottom = max(preview_top + H, bounds[3])
            if bounds[3] - bounds[1] > H:
                preview_top = bounds[1]
            self.last_frame = img.crop((0, preview_top, img.width, preview_bottom))
            img, x, y = clip_to_work_area(img, x, y, self.wa)
        drawn = self.body.show(img, x, y)
        self._confirm_show(drawn)
        if self.dance_ack is not None:
            action, sender = self.dance_ack
            self.dance_ack = None
            started = drawn and self.forced and self.forced[0] == action
            try:
                self.sock.sendto(b"ok" if started else b"interrupted", sender)
            except OSError:
                pass
        if self.lines and not (self.bubble_hide_at and now > self.bubble_hide_at):
            left, top, right, bottom = self.wa
            bw = min(int(360 * SCALE), right - left)
            content = tuple(self.lines)
            changed = content != self.bubble_content
            if changed:
                self.bubble_content = content
                self.bubble_image = draw_bubble(self.lines, bw)
            b = self.bubble_image
            bx = max(left, min(self.x + W - bw + int(40 * SCALE), right - bw))
            overlap = 0 if _STYLE == "vrm" else int(18 * SCALE)  # VRM 人物头顶贴着窗口上沿，浮窗不能压上去
            by = max(top, min(self.y - b.height + overlap, bottom - b.height))
            if changed or not self.bubble.visible or (bx, by) != (self.bubble.x, self.bubble.y):
                self.bubble.show(b, bx, by)
        elif self.lines:
            self._hide_bubble()
        self.root.after(40 if dancing or self.walk_started else 80, self.tick)

    def _confirm_show(self, drawn):
        for sender in self.show_ack:
            self._ack(sender, b"ok" if drawn else b"failed")
        self.show_ack.clear()

    def _serve_frames(self):
        """只给本机的帧服务：手机网页通过 remote.py 转发拿到银月的实时画面。"""
        import io
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        avatar = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                img = getattr(avatar, "last_frame", None)
                if img is None:
                    self.send_response(503); self.end_headers(); return
                buf = io.BytesIO()
                img.save(buf, "PNG", compress_level=1)
                data = buf.getvalue()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

        try:
            ThreadingHTTPServer(("127.0.0.1", FRAME_PORT), H).serve_forever()
        except OSError:
            pass

    def run(self):
        threading.Thread(target=self._serve_frames, daemon=True).start()
        self.tick()
        self.root.mainloop()


def main():
    kernel32.CreateMutexW(None, False, "Global\\YinYueAvatar")
    if kernel32.GetLastError() == 183:  # 已经有一个在跑
        return
    Avatar().run()


if __name__ == "__main__":
    if "--preview" in sys.argv:  # 生成几张预览图看效果
        poses = [{"outfit": name} for name in OUTFITS] + [{"eyes": "happy", "arm": "wave", "wave": 10, "mouth": "open"}]
        sheet = Image.new("RGBA", (W * len(poses), H), (235, 238, 245, 255))
        for i, ps in enumerate(poses):
            sheet.alpha_composite(person(ps), (W * i, 0))
        sheet.save(sys.argv[-1])
        b = draw_bubble([("user", "打开B站搜索钢铁侠"), ("reply", "搜好了，第一个是《钢铁侠1 完整版》，要看哪个跟我说。")], int(300 * SCALE))
        b.save(sys.argv[-1].replace(".png", "_bubble.png"))
    else:
        main()
