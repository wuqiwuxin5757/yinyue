"""银月的 3D 形象：用显卡（OpenGL）实时渲染一个原创的 3D Q 版银月，卡通着色 + 描边。

和 2D 版（avatar.py 里的 draw_yinyue / draw_wolf）参数完全一样，avatar.py 按 config 的 avatar_style 选用哪一版。
模型全部由基本形体（球、圆台、圆环）拼成，没有用任何外部模型或官方美术：
  人形：头、脸、头发（头顶/后发/刘海/鬓发）、狐耳、眼睛、腮红、嘴、交领长袍、腰带蝴蝶结、广袖、大尾巴、发饰
  小狼：身体、头、吻部、耳朵、四条腿（小跑步态）、尾巴、额头新月
着色：三段明暗 + 边缘光，再用“背面外扩”画一圈深色描边，看起来像动漫里的 3D 角色。
"""
import math

import moderngl
import numpy as np
from PIL import Image

# ======================================================================= 网格

def _sphere(lat=20, lon=28):
    v, idx = [], []
    for i in range(lat + 1):
        th = math.pi * i / lat
        for j in range(lon + 1):
            ph = 2 * math.pi * j / lon
            v.append((math.sin(th) * math.cos(ph), math.cos(th), math.sin(th) * math.sin(ph)))
    for i in range(lat):
        for j in range(lon):
            a, b = i * (lon + 1) + j, (i + 1) * (lon + 1) + j
            idx += [a, b, a + 1, a + 1, b, b + 1]
    p = np.array(v, "f4")
    return p, p.copy(), np.array(idx, "i4")


def _frustum(seg=28):
    """y 从 0 到 1，底半径 1、顶半径 1 的圆柱；粗细由模型矩阵和 taper 统一控制不了，所以单独做圆台。"""
    return _cone(1.0, 1.0, seg)


def _cone(r0, r1, seg=28):
    """底面 y=0 半径 r0，顶面 y=1 半径 r1 的圆台（含上下盖）。"""
    v, n, idx = [], [], []
    slope = r0 - r1
    for j in range(seg + 1):
        a = 2 * math.pi * j / seg
        c, s = math.cos(a), math.sin(a)
        nn = np.array([c, slope, s]); nn /= np.linalg.norm(nn)
        v += [(r0 * c, 0, r0 * s), (r1 * c, 1, r1 * s)]
        n += [tuple(nn), tuple(nn)]
    for j in range(seg):
        a = j * 2
        idx += [a, a + 1, a + 2, a + 2, a + 1, a + 3]
    for y, r, ny in ((0, r0, -1), (1, r1, 1)):  # 盖子
        if r <= 0:
            continue
        c0 = len(v)
        v.append((0, y, 0)); n.append((0, ny, 0))
        for j in range(seg + 1):
            a = 2 * math.pi * j / seg
            v.append((r * math.cos(a), y, r * math.sin(a))); n.append((0, ny, 0))
        for j in range(seg):
            idx += [c0, c0 + 1 + j, c0 + 2 + j]
    return np.array(v, "f4"), np.array(n, "f4"), np.array(idx, "i4")


def _torus(arc=2 * math.pi, R=1.0, r=0.25, seg=32, tube=12):
    """在 xy 平面上、从角度 0 绕到 arc 的一段圆环（用来做弯弯的眼睛、嘴、新月）。"""
    v, n, idx = [], [], []
    for i in range(seg + 1):
        a = arc * i / seg
        cx, cy = R * math.cos(a), R * math.sin(a)
        for j in range(tube + 1):
            b = 2 * math.pi * j / tube
            nx, ny, nz = math.cos(b) * math.cos(a), math.cos(b) * math.sin(a), math.sin(b)
            v.append((cx + r * nx, cy + r * ny, r * nz)); n.append((nx, ny, nz))
    for i in range(seg):
        for j in range(tube):
            a0, a1 = i * (tube + 1) + j, (i + 1) * (tube + 1) + j
            idx += [a0, a1, a0 + 1, a0 + 1, a1, a1 + 1]
    return np.array(v, "f4"), np.array(n, "f4"), np.array(idx, "i4")


FACE_SHAPES = {  # 可选脸型：narrow 下巴收窄程度, power 收窄从多靠下开始（越大越只收最下面）, elong 下巴拉长, wide 脸颊宽度
    "圆脸": dict(narrow=0.0, power=2.0, elong=0.0, wide=1.0),
    "鹅蛋脸": dict(narrow=0.22, power=2.2, elong=0.06, wide=0.97),
    "软萌小尖下巴": dict(narrow=0.32, power=3.2, elong=0.1, wide=1.0),
    "瓜子脸": dict(narrow=0.42, power=1.4, elong=0.2, wide=1.0),
}
FACE_SHAPE = "鹅蛋脸"


def _face(narrow=None, power=None, elong=None, wide=None):
    """变形球做的脸：上半部分是圆的，下半部分按参数往下巴收。"""
    sh = FACE_SHAPES[FACE_SHAPE]
    narrow = sh["narrow"] if narrow is None else narrow
    power = sh["power"] if power is None else power
    elong = sh["elong"] if elong is None else elong
    wide = sh["wide"] if wide is None else wide
    p, _, idx = _sphere(28, 36)
    p = p.copy()
    y = p[:, 1]
    low = np.clip(-y, 0, 1)                       # 0（脸颊以上）→ 1（最下面）
    p[:, 0] *= wide * (1 - narrow * low ** power)
    p[:, 2] *= 1 - narrow * 0.6 * low ** power
    p[:, 1] = np.where(y < 0, y * (1 + elong * low), y)
    return p, _vertex_normals(p, idx), idx


def _vertex_normals(p, idx):
    """变形后重新算法线：把相邻三角形的法线平均一下。"""
    tri = idx.reshape(-1, 3)
    fn = np.cross(p[tri[:, 1]] - p[tri[:, 0]], p[tri[:, 2]] - p[tri[:, 0]])
    n = np.zeros_like(p)
    for k in range(3):
        np.add.at(n, tri[:, k], fn)
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-9
    centre = p - p.mean(axis=0)
    n[(n * centre).sum(axis=1) < 0] *= -1         # 统一朝外
    return n.astype("f4")


def _tail(t0, t1, rings=56, seg=40, smooth=False):
    """蓬松的狐尾：沿一条 S 形曲线放样的管子，根部细、中段最胖、尾尖收尖，表面带一点毛茸茸的起伏。
    t0~t1 取曲线的一段；银月的尾巴主体和白色尾尖共用同一个接缝。"""
    def centre(t):  # 从腰后往外伸：先往下垂，后半段往上翘（坐标：+z 往身后，+y 往上）
        return np.array([0.12 * t, -0.32 * math.sin(math.pi * t * 0.85) + 0.42 * t ** 2.2, 0.62 * t])

    def base_r(t):
        return 0.03 + 0.12 * math.sin(math.pi * min(t, 0.6) / 1.2) ** 0.7     # 根部细，中后段最粗（比之前瘦）

    def radius(t, a):
        r = base_r(t)
        if t > 0.72:   # 尾端柔和地收拢成一簇（不是圆球，也不是尖锥）
            u = (t - 0.72) / 0.28
            r = base_r(0.72) * (1 - u ** 1.8) ** 0.55 + 0.006
        if smooth:
            return r
        # 毛簇：沿着尾巴的一道道毛束，往外翘的部分更明显
        tuft = abs(math.sin(5 * a + 9 * t)) ** 3
        fluff = 1 + 0.16 * tuft + 0.05 * math.sin(13 * a - 27 * t)
        return r * fluff

    v, idx = [], []
    for i in range(rings + 1):
        t = t0 + (t1 - t0) * i / rings
        c = centre(t)
        tan = centre(min(t + 0.01, 1)) - centre(max(t - 0.01, 0))
        tan /= np.linalg.norm(tan)
        axis = [1, 0, 0] if smooth or abs(tan[1]) >= 0.9 else [0, 1, 0]
        side = np.cross(tan, axis)
        side /= np.linalg.norm(side)
        up = np.cross(side, tan)
        for j in range(seg + 1):
            a = 2 * math.pi * j / seg
            v.append(c + radius(t, a) * (math.cos(a) * side + math.sin(a) * up))
    for i in range(rings):
        for j in range(seg):
            a0, a1 = i * (seg + 1) + j, (i + 1) * (seg + 1) + j
            idx += [a0, a1, a0 + 1, a0 + 1, a1, a1 + 1]
    p = np.array(v, "f4")
    idx = np.array(idx, "i4")
    return p, _vertex_normals(p, idx), idx


def _fix_winding(p, n, idx):
    """把每个三角形都调成从外面看是逆时针（OpenGL 的正面），否则背面剔除会把正面剔掉。"""
    tri = idx.reshape(-1, 3)
    v0, v1, v2 = p[tri[:, 0]], p[tri[:, 1]], p[tri[:, 2]]
    face_n = np.cross(v1 - v0, v2 - v0)
    avg_n = n[tri[:, 0]] + n[tri[:, 1]] + n[tri[:, 2]]
    flip = (face_n * avg_n).sum(axis=1) < 0
    tri[flip] = tri[flip][:, [0, 2, 1]]
    return p, n, tri.reshape(-1).astype("i4")


# ======================================================================= 矩阵

def T(x=0, y=0, z=0):
    m = np.eye(4, dtype="f4"); m[:3, 3] = (x, y, z); return m


def S(x=1, y=None, z=None):
    y = x if y is None else y
    z = x if z is None else z
    return np.diag([x, y, z, 1]).astype("f4")


def RX(deg):
    a = math.radians(deg); c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0, 0], [0, c, -s, 0], [0, s, c, 0], [0, 0, 0, 1]], "f4")


def RY(deg):
    a = math.radians(deg); c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s, 0], [0, 1, 0, 0], [-s, 0, c, 0], [0, 0, 0, 1]], "f4")


def RZ(deg):
    a = math.radians(deg); c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0, 0], [s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], "f4")


def M(*ms):
    out = np.eye(4, dtype="f4")
    for m in ms:
        out = out @ m
    return out


def _perspective(fov, aspect, near, far):
    f = 1 / math.tan(math.radians(fov) / 2)
    return np.array([[f / aspect, 0, 0, 0], [0, f, 0, 0],
                     [0, 0, (far + near) / (near - far), 2 * far * near / (near - far)], [0, 0, -1, 0]], "f4")


def _look_at(eye, target):
    eye, target = np.array(eye, "f4"), np.array(target, "f4")
    f = target - eye; f /= np.linalg.norm(f)
    r = np.cross(f, [0, 1, 0]); r /= np.linalg.norm(r)
    u = np.cross(r, f)
    m = np.eye(4, dtype="f4")
    m[0, :3], m[1, :3], m[2, :3] = r, u, -f
    m[:3, 3] = -m[:3, :3] @ eye
    return m


# ======================================================================= 着色器

VS = """
#version 330
uniform mat4 mvp; uniform mat4 model; uniform mat3 nmat; uniform float outline;
in vec3 in_pos; in vec3 in_norm;
out vec3 v_norm; out vec3 v_local; out vec3 v_world;
void main() {
    v_local = in_pos;
    vec3 p = in_pos;
    vec4 wp = model * vec4(p, 1.0);
    vec3 wn = normalize(nmat * in_norm);
    wp.xyz += wn * outline;            // 描边：沿法线往外撑一圈
    v_world = wp.xyz; v_norm = wn;
    gl_Position = mvp * wp;
}
"""
FS = """
#version 330
uniform vec3 color; uniform vec4 clip; uniform int is_outline; uniform vec3 light; uniform vec3 eye; uniform float flat_shade;
in vec3 v_norm; in vec3 v_local; in vec3 v_world;
out vec4 frag;
void main() {
    if (dot(v_local, clip.xyz) + clip.w < 0.0) discard;   // 每个零件可以切掉一部分（比如头发不盖住脸）
    if (is_outline == 1) { frag = vec4(color, 1.0); return; }
    vec3 n = normalize(v_norm);
    vec3 v = normalize(eye - v_world);
    if (dot(n, v) < 0.0) n = -n;
    float d = dot(n, normalize(light));
    float band = d > 0.45 ? 1.0 : (d > -0.05 ? 0.86 : 0.72);     // 卡通的三段明暗
    band = mix(band, 1.0, flat_shade);
    float rim = pow(1.0 - max(dot(n, v), 0.0), 3.0) * 0.22;     // 边缘光
    frag = vec4(min(color * band + rim, 1.0), 1.0);
}
"""


class Renderer:
    def __init__(self, width, height, ss=2):
        self.ss = ss
        self.ctx = moderngl.create_standalone_context()
        self.prog = self.ctx.program(vertex_shader=VS, fragment_shader=FS)
        self.fbo = None
        self.resize(width, height)
        self.meshes = {}
        for name, (p, n, idx) in {"sphere": _sphere(), "face": _face(), "tail_body": _tail(0.0, 0.78, smooth=True),
                                   "tail_tip": _tail(0.78, 1.0, smooth=True), "wolf_tail": _tail(0.0, 1.0, smooth=True),
                                   "cyl": _cone(1, 1), "cone": _cone(1, 0.02),
                                   "frustum": _cone(1, 0.62), "arc": _torus(math.pi, 1, 0.22),
                                   "moon": _torus(math.pi * 1.25, 1, 0.42)}.items():
            p, n, idx = _fix_winding(p, n, idx)
            vbo = self.ctx.buffer(np.hstack([p, n]).astype("f4").tobytes())
            ibo = self.ctx.buffer(idx.tobytes())
            self.meshes[name] = self.ctx.vertex_array(self.prog, [(vbo, "3f 3f", "in_pos", "in_norm")], ibo)
        self.parts = []

    def resize(self, width, height):
        if self.fbo is not None and (self.w, self.h) == (width, height):
            return
        size = (width * self.ss, height * self.ss)
        color = self.ctx.renderbuffer(size, 4)
        depth = self.ctx.depth_renderbuffer(size)
        fbo = self.ctx.framebuffer(color, depth)
        if self.fbo is not None:
            self.fbo.release()
            self.color.release()
            self.depth.release()
        self.w, self.h = width, height
        self.color, self.depth, self.fbo = color, depth, fbo

    # ---- 场景搭建
    def add(self, mesh, model, color, clip=(0, 0, 0, 1), outline=True, flat=0.0):
        self.parts.append((mesh, model, tuple(c / 255 for c in color), clip, outline, flat))

    def render(self, eye=(0, 1.9, 14), target=(0, 1.25, 0), fov=21, outline_w=0.035, outline_color=(70, 72, 110)):
        self.fbo.use()
        self.ctx.clear(0, 0, 0, 0)
        vp = _perspective(fov, self.w / self.h, 0.1, 50) @ _look_at(eye, target)
        return self.render_parts(vp, eye, outline_w, outline_color)

    def render_parts(self, vp, eye, outline_w=0.006, outline_color=(70, 72, 110)):
        """把排队的零件画进当前画面（不清空，可以叠在 VRM 模型上），然后读出图片。"""
        ctx = self.ctx
        self.fbo.use()
        ctx.enable(moderngl.DEPTH_TEST | moderngl.CULL_FACE)
        ctx.disable(moderngl.BLEND)
        ctx.depth_mask = True
        light = (0.5, 0.9, 0.8)
        self.prog["light"].value = light
        self.prog["eye"].value = tuple(eye)
        for pass_outline in (True, False):
            ctx.cull_face = "front" if pass_outline else "back"
            for mesh, model, color, clip, has_outline, flat in self.parts:
                if pass_outline and not has_outline:
                    continue
                nmat = np.linalg.inv(model[:3, :3]).T
                self.prog["mvp"].write(vp.T.astype("f4").tobytes())  # 着色器里自己乘 model，这里只给视图投影矩阵
                self.prog["model"].write(model.T.astype("f4").tobytes())
                self.prog["nmat"].write(nmat.T.astype("f4").tobytes())
                self.prog["outline"].value = outline_w if pass_outline else 0.0
                self.prog["is_outline"].value = 1 if pass_outline else 0
                self.prog["color"].value = tuple(c / 255 for c in outline_color) if pass_outline else color
                self.prog["clip"].value = clip
                self.prog["flat_shade"].value = flat
                self.meshes[mesh].render()
        self.parts = []
        data = self.fbo.read(components=4, alignment=1)
        img = Image.frombytes("RGBA", (self.w * self.ss, self.h * self.ss), data).transpose(Image.FLIP_TOP_BOTTOM)
        return img.resize((self.w, self.h), Image.LANCZOS)


# ======================================================================= 角色

HAIR, HAIR_SHADE, HAIR_LIGHT = (232, 236, 246), (186, 194, 218), (250, 251, 255)
SKIN, BLUSH = (255, 234, 222), (255, 178, 188)
EYE, EYE_DARK = (118, 104, 222), (58, 46, 140)
EAR_IN = (255, 190, 206)

_r = None


def _renderer(w, h):
    global _r
    if _r is None:
        _r = Renderer(w, h)
    else:
        _r.resize(w, h)
    return _r


def draw_yinyue3d(p: dict, outfits: dict, default_outfit: str, w: int, h: int) -> Image.Image:
    r = _renderer(w, h)
    o = outfits.get(p.get("outfit"), outfits[default_outfit])
    bob = p.get("bob", 0) * 0.025 + p.get("jump", 0) * 0.05
    yaw = 18 + p.get("spin", 0) + p.get("look", 0) * 8      # 默认微微侧身，看得出是立体的
    root = M(T(p.get("dx", 0) * 0.06, bob, 0), RY(yaw))

    # ---- 尾巴：一串球从屁股后面翘起来
    tail_root = M(root, T(0.35, 0.05, -0.45), RY(-35 + p.get("tail", 0) * 1.6))
    for i in range(12):
        t = i / 11
        rad = 0.16 + 0.34 * math.sin(t * math.pi * 0.82)
        pos = (0.55 * t, 1.25 * t ** 1.25, -0.25 * t)
        col = HAIR if i < 9 else HAIR_LIGHT
        r.add("sphere", M(tail_root, T(*pos), S(rad)), col)

    # ---- 披风（冬雪斗篷才有）：在长袍外面、只留背后和两侧，领口一圈白毛
    if o["cape"]:
        r.add("frustum", M(root, T(0, -0.68, -0.02), S(1.06, 1.5, 0.9)), o["cape"], clip=(0, 0, -1, 0.15))
        r.add("sphere", M(root, T(0, 0.8, 0), S(0.72, 0.2, 0.62)), (255, 255, 255))

    # ---- 衣服：长袍（圆台）+ 衣摆滚边 + 交领 + 腰带 + 广袖
    r.add("frustum", M(root, T(0, -0.65, 0), S(0.98, 1.38, 0.82)), o["robe"])
    r.add("cyl", M(root, T(0, -0.66, 0), S(1.0, 0.07, 0.84)), o["trim"], outline=False)       # 衣摆滚边
    r.add("cyl", M(root, T(0, -0.57, 0), S(1.0, 0.035, 0.84)), o["pattern"], outline=False)
    r.add("cyl", M(root, T(0, 0.12, 0), S(0.81, 0.15, 0.69)), o["sash"])                       # 腰带
    r.add("cyl", M(root, T(0, 0.265, 0), S(0.80, 0.025, 0.68)), o["accent"], outline=False)
    for side in (-1, 1):                                                                        # 蝴蝶结 + 飘带
        r.add("sphere", M(root, T(0.16 * side, 0.2, 0.68), RZ(25 * side), S(0.16, 0.1, 0.06)), o["sash"])
        r.add("cyl", M(root, T(0.07 * side, -0.3, 0.7), RZ(-8 * side + p.get("tail", 0) * 0.3), S(0.045, 0.45, 0.02)), o["sash"])
    r.add("sphere", M(root, T(0, 0.2, 0.71), S(0.07)), o["accent"])
    # 交领（右衽：左襟压右襟），里衬从领口露出来
    r.add("sphere", M(root, T(0, 0.66, 0.42), S(0.2, 0.17, 0.06)), o["inner"], outline=False)
    for side, z in ((1, 0.5), (-1, 0.52)):
        r.add("cyl", M(root, T(0, 0.5, z), RZ(-38 * side), S(0.05, 0.45, 0.04)), o["trim"], outline=False)
    # 玉佩
    r.add("sphere", M(root, T(-0.4, -0.1, 0.68), S(0.08, 0.1, 0.05)), o["inner"])
    r.add("cyl", M(root, T(-0.4, -0.34, 0.68), S(0.018, 0.16, 0.018)), o["trim"], outline=False)

    def sleeve(side, pitch, roll, length=0.95):
        """广袖：肩膀处细、袖口宽，袖口有一圈滚边，手从袖口露出来。"""
        base = M(root, T(0.6 * side, 0.78, 0), RZ(roll * side), RX(pitch))
        r.add("cone", M(base, RX(180), S(0.22, 0.01, 0.22)), o["robe"])               # 肩头圆过渡
        r.add("sphere", M(base, S(0.24)), o["robe"], outline=False)
        r.add("frustum", M(base, T(0, -length, 0), RX(0), S(0.36, length, 0.36)), o["robe"])
        r.add("cyl", M(base, T(0, -length - 0.02, 0), S(0.37, 0.06, 0.37)), o["trim"], outline=False)
        r.add("sphere", M(base, T(0, -length - 0.12, 0.05), S(0.12)), SKIN)

    arm = p.get("arm", "down")
    if arm == "up":
        sleeve(-1, 0, 160)
        sleeve(1, 0, 160)
    else:
        sleeve(-1, 8, 16)
        if arm == "wave":
            sleeve(1, -10, 150 + p.get("wave", 0))
        else:
            sleeve(1, 8, 16)

    # ---- 头（单独一组，可以歪头、转头）
    head = M(root, T(0, 1.05, 0), RZ(-p.get("tilt", 0)), RY(p.get("look", 0) * 14), T(0, 0.9, 0))
    r.add("face", M(head, S(0.9, 0.9, 0.9)), SKIN, flat=0.6)                                  # 瓜子脸（偏平涂，动漫脸不要太重的阴影）
    r.add("sphere", M(head, T(0, 0.08, -0.06), S(1.08, 1.05, 1.06)), HAIR, clip=(0, 1, -0.3, 0.0), outline=False)  # 头顶+后脑的头发（只切掉前脸下半部分）
    r.add("sphere", M(head, T(0, -0.55, -0.32), S(1.0, 1.25, 0.72)), HAIR_SHADE, clip=(0, 0, -1, 0.05), outline=False)   # 后面的长发
    for x, ln, rot in ((-0.7, 0.7, 14), (-0.42, 0.62, 7), (-0.14, 0.55, 2), (0.14, 0.58, -2), (0.42, 0.62, -7),
                       (0.7, 0.7, -14)):  # 刘海：一排宽宽的发束
        r.add("cone", M(head, T(x, 0.66, 0.66 - abs(x) * 0.42), RZ(180 + rot), RX(-22), S(0.3, ln, 0.18)), HAIR)
    for side in (-1, 1):                                                                       # 鬓发
        r.add("sphere", M(head, T(0.78 * side, -0.45, 0.2), RZ(10 * side), S(0.2, 0.8, 0.28)), HAIR)
    # 狐耳
    for side in (-1, 1):
        ear = M(head, T(0.52 * side, 0.68, -0.05), RZ(-22 * side + p.get("ear", 0) * 2 * side), RX(-8))
        r.add("cone", M(ear, S(0.3, 0.75, 0.2)), HAIR)
        r.add("cone", M(ear, T(0, 0.04, 0.09), S(0.17, 0.55, 0.06)), EAR_IN, outline=False)
    # 眼睛
    eyes = p.get("eyes", "open")
    lk = p.get("look", 0) * 0.04
    for i, side in enumerate((-1, 1)):
        e = ("happy" if i == 0 else "open") if eyes == "wink" else eyes
        ex = M(head, T(0.33 * side, -0.08, 0.8), RY(22 * side), S(1.05))
        if e == "open":
            r.add("sphere", M(ex, S(0.17, 0.25, 0.06)), EYE, outline=False)
            r.add("sphere", M(ex, T(lk, -0.03, 0.03), S(0.11, 0.17, 0.05)), EYE_DARK, outline=False, flat=1)
            r.add("sphere", M(ex, T(-0.05 + lk, 0.08, 0.07), S(0.05, 0.06, 0.03)), (255, 255, 255), outline=False, flat=1)
            r.add("sphere", M(ex, T(0.05 + lk, -0.1, 0.07), S(0.025)), (255, 255, 255), outline=False, flat=1)
            r.add("cyl", M(ex, T(0, 0.27, 0.02), RZ(90 + 6 * side), S(0.025, 0.22, 0.025), T(0, -0.5, 0)), (70, 70, 110), outline=False, flat=1)
        elif e == "happy":
            r.add("arc", M(ex, T(0, -0.04, 0.04), S(0.15, 0.13, 0.12)), EYE_DARK, outline=False, flat=1)
        else:
            r.add("arc", M(ex, T(0, 0.06, 0.04), RZ(180), S(0.15, 0.1, 0.12)), EYE_DARK, outline=False, flat=1)
    # 腮红
    for side in (-1, 1):
        r.add("sphere", M(head, T(0.48 * side, -0.38, 0.68), RY(35 * side), S(0.13, 0.06, 0.03)),
              tuple(min(255, int(c * (0.9 + p.get("blush", 110) / 1000))) for c in BLUSH), outline=False, flat=0.6)
    # 嘴
    mouth = p.get("mouth", "smile")
    mz = M(head, T(0, -0.62, 0.8), S(0.8))
    if mouth in ("open", "yawn"):
        r.add("sphere", M(mz, S(0.09, 0.08 if mouth == "open" else 0.13, 0.04)), (196, 84, 104), outline=False, flat=1)
    elif mouth == "o":
        r.add("sphere", M(mz, S(0.05, 0.06, 0.03)), (196, 84, 104), outline=False, flat=1)
    elif mouth == "flat":
        r.add("cyl", M(mz, RZ(90), S(0.018, 0.14, 0.018), T(0, -0.5, 0)), (90, 80, 120), outline=False, flat=1)
    else:
        r.add("arc", M(mz, T(0, 0.06, 0), RZ(180), S(0.1, 0.08, 0.08)), (90, 80, 120), outline=False, flat=1)
    # 发饰
    acc, hx = o["hair"], M(head, T(0.74, 0.7, 0.42), RY(35))
    if acc in ("moon", "star_moon"):
        r.add("moon", M(hx, RZ(110), S(0.22, 0.22, 0.12)), o["accent"])
        if acc == "star_moon":
            r.add("sphere", M(head, T(-0.62, 0.62, 0.45), S(0.08)), o["accent"])
    elif acc == "buyao":
        r.add("cyl", M(hx, RZ(60), S(0.03, 0.5, 0.03), T(0, -0.5, 0)), o["accent"])
        r.add("sphere", M(hx, S(0.1)), o["trim"])
        for k in range(3):
            sw = p.get("tail", 0) * 0.01
            r.add("cyl", M(hx, T(-0.08 + k * 0.08, -0.05, 0), RZ(sw * 30), S(0.008, -0.25 - 0.08 * (k == 1), 0.008)), o["accent"], outline=False)
            r.add("sphere", M(hx, T(-0.08 + k * 0.08 + sw, -0.3 - 0.08 * (k == 1), 0), S(0.045)), o["pattern"])
    elif acc == "flower":
        for k in range(5):
            a = k * 72
            r.add("sphere", M(hx, RZ(a), T(0, 0.11, 0), S(0.09, 0.09, 0.05)), o["sash"])
        r.add("sphere", M(hx, T(0, 0, 0.03), S(0.06)), o["accent"])
    elif acc == "ribbon":
        for side in (-1, 1):
            r.add("cone", M(hx, RZ(90 * side), S(0.13, 0.24, 0.06)), o["sash"])
        r.add("sphere", M(hx, S(0.06)), o["trim"])
    elif acc == "pompom":
        r.add("sphere", M(hx, S(0.13)), (255, 255, 255))
        r.add("sphere", M(hx, T(-0.16, -0.16, 0), S(0.1)), (255, 255, 255))

    img = r.render()
    return _extras(img, p, w, h)


def draw_wolf3d(phase: float, facing: int, w: int, h: int) -> Image.Image:
    """对角腿交替着地，抬脚阶段弯膝，落地阶段伸开。"""
    r = _renderer(w, h)
    bounce = (1 - math.cos(phase * 2)) * 0.025
    root = M(T(0, bounce, 0), RY(67 * facing))
    body = M(root, T(0, 0.83, 0), RX(3 * math.sin(phase * 2)))
    r.add("sphere", M(body, S(0.43, 0.34, 0.9)), HAIR)
    r.add("sphere", M(body, T(0, 0.05, 0.50), RX(-18), S(0.44, 0.42, 0.54)), HAIR, outline=False)
    r.add("sphere", M(body, T(0, -0.07, 0.66), RX(-20), S(0.31, 0.31, 0.34)), HAIR_LIGHT, outline=False, flat=0.2)
    for side, z, offset in ((-1, 0.54, 0), (1, 0.54, math.pi), (-1, -0.55, math.pi), (1, -0.55, 0)):
        step = phase + offset
        lift = max(0, math.cos(step))
        front = z > 0
        upper = math.sin(step) * 28 + (0 if front else -19)
        bend = (24 + 42 * lift) * (1 if front else -1)
        coat = HAIR if side == -facing else HAIR_SHADE
        hip = M(body, T(side * 0.27, -0.13, z), RX(upper))
        knee = M(hip, T(0, -0.32, 0), RX(bend))
        paw = M(knee, T(0, -0.31, 0), RX(-upper - bend + lift * 12))
        r.add("sphere", M(hip, T(0, -0.12, 0), S(0.12 if front else 0.19, 0.26, 0.14 if front else 0.24)), coat, outline=False)
        r.add("sphere", M(knee, T(0, -0.14, 0), S(0.084, 0.22, 0.086)), coat, outline=False)
        r.add("sphere", M(paw, T(0, -0.018, 0.046), S(0.113, 0.078, 0.153)), HAIR_LIGHT, outline=False)

    head = M(body, T(0, 0.38, 0.87), RX(-3 + math.sin(phase * 2) * 4))
    r.add("sphere", M(head, S(0.41, 0.36, 0.43)), HAIR)
    r.add("sphere", M(head, T(0, -0.14, 0.31), RX(-10), S(0.27, 0.18, 0.36)), HAIR_LIGHT, outline=False)
    r.add("sphere", M(head, T(0, -0.09, 0.64), S(0.088, 0.054, 0.064)), (50, 54, 76), outline=False, flat=0.8)
    for side in (-1, 1):
        ear = M(head, T(0.25 * side, 0.24, -0.05), RZ(-18 * side), RX(-12))
        r.add("cone", M(ear, S(0.16, 0.40, 0.12)), HAIR)
        r.add("cone", M(ear, T(0, 0.035, 0.080), S(0.09, 0.26, 0.046)), EAR_IN, outline=False)
        eye = M(head, T(0.30 * side, 0.045, 0.263), RY(46 * side), RZ(-12 * side))
        r.add("sphere", M(eye, S(0.079, 0.086, 0.038)), (69, 60, 115), outline=False, flat=0.9)
        r.add("sphere", M(eye, T(0.015, 0.022, 0.034), S(0.023)), (250, 251, 255), outline=False, flat=1)
    r.add("moon", M(head, T(0, 0.21, 0.353), RZ(110), S(0.097, 0.097, 0.05)), (245, 210, 110), outline=False)
    tail = M(body, T(0, 0.06, -0.73), RY(180 + 12 * math.sin(phase)), RX(8), S(1.6, 1.25, 1.65))
    r.add("wolf_tail", tail, HAIR, outline=False, flat=0.25)
    return r.render(eye=(0, 2.54, 13.2), target=(0, 2.54, 0), fov=24, outline_w=0.019)


def _extras(img, p, w, h):
    """zzz、思考小点、音符、爱心、闪光这些 2D 小特效直接画在图上。"""
    from PIL import ImageDraw, ImageFont
    extra = p.get("extra")
    if not extra:
        return img
    d = ImageDraw.Draw(img)
    k = w / 186
    ph = p.get("phase", 0)
    col = (140, 150, 220, 230)
    if extra == "zzz":
        for i, s in enumerate((10, 13, 16)):
            fz = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", int(s * k))
            d.text((132 * k + i * 9 * k, (40 - i * 16 - ph * 6 % 6) * k), "z", font=fz, fill=col)
    elif extra == "think":
        for i in range(int(ph * 3) % 4):
            d.ellipse(((132 + i * 11) * k, 24 * k, (139 + i * 11) * k, 31 * k), fill=col)
    elif extra == "note":
        sym = ImageFont.truetype(r"C:\Windows\Fonts\seguisym.ttf", int(16 * k))
        d.text((134 * k, (16 - ph * 4 % 6) * k), "♪", font=sym, fill=col)
    elif extra == "heart":
        t = ph % 3 / 3
        cx, cy, rr = 140 * k, (34 - 14 * t) * k, 4.5 * k
        a = int(240 * (1 - t))
        d.ellipse((cx - 2 * rr, cy - rr, cx, cy + rr), fill=(250, 120, 150, a))
        d.ellipse((cx, cy - rr, cx + 2 * rr, cy + rr), fill=(250, 120, 150, a))
        d.polygon((cx - 2 * rr, cy + 1, cx + 2 * rr, cy + 1, cx, cy + 2.6 * rr), fill=(250, 120, 150, a))
    elif extra == "sparkle":
        cx, cy, s = 146 * k, 30 * k, 9 * k
        d.polygon((cx, cy - s, cx + s / 3, cy - s / 3, cx + s, cy, cx + s / 3, cy + s / 3, cx, cy + s,
                   cx - s / 3, cy + s / 3, cx - s, cy, cx - s / 3, cy - s / 3), fill=(245, 210, 110, 240))
    return img
