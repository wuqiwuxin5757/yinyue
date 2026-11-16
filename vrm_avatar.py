"""用专业动漫模型（VRM）渲染银月：骨骼动作 + 表情 + 卡通着色。

模型：models/vrm/HairSample_Female.vrm（VRoid Studio 官方示例模型，CC0 授权，可自由修改）。
改造：头发和兽耳改成银白、瞳色改成蓝紫、加一条狐尾、按换装配色给衣服上色。
接口和 avatar3d.draw_yinyue3d 一样，avatar.py 按 config 的 avatar_style="vrm" 选用。

实现要点：
- 自己解析 .vrm（就是 glb）：网格、骨骼蒙皮、表情形态（morph target）、贴图、VRoid 的 MToon 材质参数
- 顶点着色器里做骨骼蒙皮（每个顶点最多 4 根骨骼）
- 表情在 CPU 上混合形态后更新顶点缓冲（只有脸那一个网格有表情，开销很小）
- 卡通着色：亮部/暗部两色分界（用材质里的 _Color 和 _ShadeColor），加描边（背面外扩）
"""
import io
import json
import math
import struct
from pathlib import Path

import moderngl
import numpy as np
from PIL import Image

import avatar3d as a3

MODEL = Path(__file__).parent / "models" / "vrm" / "HairSample_Female.vrm"
MAX_JOINTS = 200  # 有的 VRoid 模型骨骼超过 128 根

VS = """
#version 330
uniform mat4 vp; uniform mat4 joints[%d]; uniform float outline; uniform int skinned; uniform mat4 model;
in vec3 in_pos; in vec3 in_norm; in vec2 in_uv; in vec4 in_joints; in vec4 in_weights;
out vec3 v_norm; out vec2 v_uv; out vec3 v_world;
void main() {
    mat4 skin = model;
    if (skinned == 1) {
        skin = in_weights.x * joints[int(in_joints.x)] + in_weights.y * joints[int(in_joints.y)]
             + in_weights.z * joints[int(in_joints.z)] + in_weights.w * joints[int(in_joints.w)];
    }
    vec4 wp = skin * vec4(in_pos, 1.0);
    vec3 wn = normalize(mat3(skin) * in_norm);
    wp.xyz += wn * outline;
    v_world = wp.xyz; v_norm = wn; v_uv = in_uv;
    gl_Position = vp * wp;
}
""" % MAX_JOINTS

FS = """
#version 330
uniform sampler2D tex; uniform int has_tex; uniform vec4 color; uniform vec3 shade; uniform float shade_shift; uniform float toony;
uniform float cutoff; uniform int is_outline; uniform vec3 outline_color; uniform vec3 light; uniform vec3 eye;
uniform vec3 tint; uniform float tint_amount; uniform vec2 tint_curve; uniform float cut_dark;
uniform int pat_on; uniform vec3 pat_a; uniform vec3 pat_b; uniform vec3 pat_line; uniform float pat_scale;
uniform int stock_on; uniform vec3 stock_color; uniform float stock_density; uniform float stock_top; uniform float root_y;
in vec3 v_norm; in vec2 v_uv; in vec3 v_world;
out vec4 frag;
void main() {
    vec4 base = color;
    if (has_tex == 1) base *= texture(tex, v_uv);
    if (base.a < cutoff) discard;
    // 换色：先按亮度去色，再乘上目标颜色（头发改银白、衣服按换装配色）
    float lum = dot(base.rgb, vec3(0.299, 0.587, 0.114));
    if (lum < cut_dark) discard;   // 去掉原贴图里深色的那层（比如制服的马甲），只留里面的衬衫
    base.rgb = mix(base.rgb, tint * clamp(tint_curve.x + tint_curve.y * lum, 0.0, 1.08), tint_amount);
    if (pat_on == 1) {   // 格纹（韩系格子裙）：两个方向的宽条纹交叉 + 细线，保留原贴图的褶皱明暗
        vec2 g = fract(v_uv * pat_scale);
        float bands = (step(0.5, g.x) + step(0.5, g.y)) * 0.5;
        float lines = max(1.0 - smoothstep(0.0, 0.035, abs(g.x - 0.25)), 1.0 - smoothstep(0.0, 0.035, abs(g.y - 0.25)));
        vec3 plaid = mix(mix(pat_a, pat_b, bands), pat_line, lines * 0.85);
        base.rgb = plaid * clamp(0.62 + 0.55 * lum, 0.0, 1.1);
    }
    if (is_outline == 1) { frag = vec4(outline_color * base.rgb * 0.55, 1.0); return; }
    vec3 n = normalize(v_norm);
    vec3 v = normalize(eye - v_world);
    if (dot(n, v) < 0.0) n = -n;
    if (stock_on == 1) {   // 丝袜：大腿中部往下；正对镜头处透肉，轮廓边缘更实，像真丝袜
        float h = v_world.y - root_y;
        float cover = 1.0 - smoothstep(stock_top - 0.006, stock_top + 0.006, h);
        float band = smoothstep(stock_top - 0.035, stock_top - 0.02, h) * cover;      // 袜口一圈更实
        float sheer = mix(stock_density, 1.0, pow(1.0 - max(dot(n, v), 0.0), 1.5));
        base.rgb = mix(base.rgb, stock_color, cover * clamp(sheer + band * 0.3, 0.0, 1.0));
    }
    float li = dot(n, normalize(light)) * 0.5 + 0.5;             // MToon 的算法：亮度映射到 0~1
    float lit = smoothstep(shade_shift, shade_shift + max(1.0 - toony, 0.001), li);
    vec3 col = mix(base.rgb * shade, base.rgb, lit);
    float rim = pow(1.0 - max(dot(n, v), 0.0), 4.0) * 0.18;
    frag = vec4(min(col + rim, 1.0), base.a);
}
"""


def _quat_mat(q):
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0],
                     [0, 0, 0, 1]], "f4")


def _mat_quat(m):
    x, y, z = np.diag(m)
    a, b, c, d = 1+x-y-z, 1-x+y-z, 1-x-y+z, 1+x+y+z
    candidates = (
        (a, m[1, 0]+m[0, 1], m[2, 0]+m[0, 2], m[2, 1]-m[1, 2]),
        (m[1, 0]+m[0, 1], b, m[2, 1]+m[1, 2], m[0, 2]-m[2, 0]),
        (m[2, 0]+m[0, 2], m[2, 1]+m[1, 2], c, m[1, 0]-m[0, 1]),
        (m[2, 1]-m[1, 2], m[0, 2]-m[2, 0], m[1, 0]-m[0, 1], d),
    )
    q = np.array(candidates[int(np.argmax((a, b, c, d)))])
    return q / np.linalg.norm(q)


def _euler(x=0.0, y=0.0, z=0.0):
    return a3.M(a3.RZ(z), a3.RY(y), a3.RX(x))


class VRM:
    COMP = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
    NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}

    def __init__(self, ctx, path=MODEL):
        data = path.read_bytes()
        jl = struct.unpack("<I", data[12:16])[0]
        self.g = g = json.loads(data[20:20 + jl])
        self.bin = data[20 + jl + 8:]
        self.ext = g["extensions"]["VRM"]
        self.ctx = ctx
        self.prog = ctx.program(vertex_shader=VS, fragment_shader=FS)

        # ---- 骨骼层级
        self.nodes = g["nodes"]
        self.parent = {c: i for i, n in enumerate(self.nodes) for c in n.get("children", [])}
        self.rest = []
        for n in self.nodes:
            m = np.eye(4, dtype="f4")
            if "matrix" in n:
                m = np.array(n["matrix"], "f4").reshape(4, 4).T
            else:
                t = n.get("translation", [0, 0, 0]); r = n.get("rotation", [0, 0, 0, 1]); s = n.get("scale", [1, 1, 1])
                m = a3.T(*t) @ _quat_mat(r) @ a3.S(*s)
            self.rest.append(m)
        self.order = []          # 父节点在前的遍历顺序
        roots = [i for i in range(len(self.nodes)) if i not in self.parent]
        stack = roots[::-1]
        while stack:
            i = stack.pop()
            self.order.append(i)
            stack += self.nodes[i].get("children", [])[::-1]
        self.bone = {b["bone"]: b["node"] for b in self.ext["humanoid"]["humanBones"]}
        self.name_index = {n.get("name"): i for i, n in enumerate(self.nodes)}
        skin = g["skins"][0]
        self.joints = skin["joints"]
        self.inv_bind = self._acc(skin["inverseBindMatrices"]).reshape(-1, 4, 4).transpose(0, 2, 1)

        # ---- 材质 + 贴图
        self.textures = {}
        self.matprops = {m["name"]: m for m in self.ext["materialProperties"]}
        self.materials = []
        for m in g["materials"]:
            mp = self.matprops.get(m["name"], {})
            fp, vp, tp = mp.get("floatProperties", {}), mp.get("vectorProperties", {}), mp.get("textureProperties", {})
            mode = int(fp.get("_BlendMode", 0))  # MToon：0 不透明，1 镂空，2 半透明，3 半透明且写深度
            self.materials.append(dict(
                name=m["name"], tex=self._texture(tp.get("_MainTex")),
                color=tuple(vp.get("_Color", [1, 1, 1, 1])), shade=tuple(vp.get("_ShadeColor", [0.8, 0.8, 0.85, 1])[:3]),
                shade_shift=fp.get("_ShadeShift", 0.0), toony=fp.get("_ShadeToony", 0.9), cutoff=fp.get("_Cutoff", 0.5) if mode == 1 else 0.0,
                blend=mode >= 2, zwrite=bool(fp.get("_ZWrite", 1)), cull=fp.get("_CullMode", 2),
                outline=0.0,  # 不画模型自带描边：小尺寸看不出来，而且会从后脑勺透出深色
                queue=mp.get("renderQueue", 2000)))

        # ---- 网格
        self.prims = []
        for mi, mesh in enumerate(g["meshes"]):
            for pr in mesh["primitives"]:
                a = pr["attributes"]
                pos = self._acc(a["POSITION"]).astype("f4")
                nor = self._acc(a["NORMAL"]).astype("f4")
                uv = self._acc(a["TEXCOORD_0"]).astype("f4") if "TEXCOORD_0" in a else np.zeros((len(pos), 2), "f4")
                jo = self._acc(a["JOINTS_0"]).astype("f4")
                we = self._acc(a["WEIGHTS_0"]).astype("f4")
                idx = self._acc(pr["indices"]).astype("i4")
                targets = [self._acc(t["POSITION"]).astype("f4") for t in pr.get("targets", [])]
                vbo_pos = ctx.buffer(pos.tobytes(), dynamic=bool(targets))
                vbo_rest = ctx.buffer(np.hstack([nor, uv, jo, we]).astype("f4").tobytes())
                vao = ctx.vertex_array(self.prog, [(vbo_pos, "3f", "in_pos"),
                                                   (vbo_rest, "3f 2f 4f 4f", "in_norm", "in_uv", "in_joints", "in_weights")],
                                       ctx.buffer(idx.tobytes()))
                self.prims.append(dict(mesh=mi, mesh_name=mesh["name"], mat=self.materials[pr["material"]], vao=vao, pos=pos, idx=idx, targets=targets,
                                       vbo=vbo_pos, weights=None))

        # ---- 表情：预设名 → [(网格, 形态序号, 权重)]
        self.expr = {}
        for grp in self.ext["blendShapeMaster"]["blendShapeGroups"]:
            key = grp["presetName"] if grp["presetName"] != "unknown" else grp["name"].lower()
            self.expr[key] = [(b["mesh"], b["index"], b["weight"] / 100) for b in grp["binds"]]

    def _acc(self, i):
        acc = self.g["accessors"][i]
        bv = self.g["bufferViews"][acc["bufferView"]]
        dt = self.COMP[acc["componentType"]]
        nc = self.NCOMP[acc["type"]]
        off = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
        stride = bv.get("byteStride", 0)
        item = np.dtype(dt).itemsize * nc
        if stride and stride != item:
            raw = np.frombuffer(self.bin, np.uint8, count=stride * acc["count"], offset=off).reshape(acc["count"], stride)
            return raw[:, :item].copy().view(dt).reshape(acc["count"], nc)
        return np.frombuffer(self.bin, dt, count=acc["count"] * nc, offset=off).reshape(acc["count"], nc).copy() \
            if nc > 1 else np.frombuffer(self.bin, dt, count=acc["count"], offset=off).copy()

    def _texture(self, ti):
        if ti is None:
            return None
        if ti in self.textures:
            return self.textures[ti]
        src = self.g["textures"][ti]["source"]
        bv = self.g["bufferViews"][self.g["images"][src]["bufferView"]]
        off = bv.get("byteOffset", 0)
        img = Image.open(io.BytesIO(self.bin[off:off + bv["byteLength"]])).convert("RGBA")
        tex = self.ctx.texture(img.size, 4, img.tobytes())
        tex.build_mipmaps()
        tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
        self.textures[ti] = tex
        return tex

    # ---------------------------------------------------------------- 每帧
    def pose_matrices(self, local_rot: dict, root: np.ndarray):
        """local_rot：节点号 → 附加旋转矩阵（在静止姿态基础上再转）。返回所有节点的全局矩阵。"""
        world = [None] * len(self.nodes)
        for i in self.order:
            m = self.rest[i]
            if i in local_rot:
                m = m @ local_rot[i]
            world[i] = (world[self.parent[i]] if i in self.parent else root) @ m
        return world

    def _borrowed_world(self, j, world, rig):
        """借衣服时，这件衣服的骨骼 j 在银月身上的位置。银月有同名骨骼就直接用；
        没有的（比如裙摆专用骨骼）就往上找最近一根银月也有的骨骼，按原模型里的相对位置挂上去。"""
        if not hasattr(self, "_rest_world"):
            self._rest_world = self.pose_matrices({}, np.eye(4, dtype="f4"))
            self._anchor = {}
        name = self.nodes[j].get("name")
        if name in rig.name_index:
            return world[rig.name_index[name]]
        if j not in self._anchor:
            a = j
            while a in self.parent and self.nodes[a].get("name") not in rig.name_index:
                a = self.parent[a]
            rel = np.linalg.inv(self._rest_world[a]) @ self._rest_world[j]
            self._anchor[j] = (rig.name_index.get(self.nodes[a].get("name"), rig.bone["hips"]), rel.astype("f4"))
        anc, rel = self._anchor[j]
        return world[anc] @ rel

    def set_expressions(self, weights: dict):
        """weights：表情预设名 → 0~1。把形态混合到脸的顶点上。"""
        per_mesh = {}
        for name, w in weights.items():
            for mesh, idx, bw in self.expr.get(name, []):
                if w > 0:
                    per_mesh.setdefault(mesh, {})
                    per_mesh[mesh][idx] = per_mesh[mesh].get(idx, 0) + w * bw
        for pr in self.prims:
            if not pr["targets"]:
                continue
            want = per_mesh.get(pr["mesh"], {})
            key = tuple(sorted((k, round(v, 2)) for k, v in want.items()))
            if key == pr["weights"]:
                continue
            pos = pr["pos"].copy()
            for k, v in want.items():
                pos += pr["targets"][k] * min(v, 1.0)
            pr["vbo"].write(pos.tobytes())
            pr["weights"] = key

    def draw(self, world, vp, eye, light, tints: dict, outline_color=(0.35, 0.33, 0.5), prims=None, rig=None,
             stockings=None, root_y=0.0):
        """world 是银月骨架的全局矩阵。rig=(银月模型) 时表示这是借来的衣服：按骨骼名字找到银月对应的骨骼。"""
        if rig is None:
            jm = np.stack([world[j] @ self.inv_bind[k] for k, j in enumerate(self.joints)]).astype("f4")
        else:
            jm = np.stack([self._borrowed_world(j, world, rig) @ self.inv_bind[k]
                           for k, j in enumerate(self.joints)]).astype("f4")
        jm = np.concatenate([jm, np.tile(np.eye(4, dtype="f4"), (MAX_JOINTS - len(jm), 1, 1))])
        p = self.prog
        p["joints"].write(jm.transpose(0, 2, 1).tobytes())
        p["vp"].write(vp.T.astype("f4").tobytes())
        p["model"].write(np.eye(4, dtype="f4").tobytes())
        p["skinned"].value = 1
        p["eye"].value = tuple(eye)
        p["light"].value = tuple(light)
        p["outline_color"].value = outline_color
        ctx = self.ctx
        ordered = sorted(self.prims if prims is None else prims, key=lambda q: (q["mat"]["blend"], q["mat"]["queue"]))
        for pass_outline in (True, False):
            for pr in ordered:
                mat = pr["mat"]
                if pass_outline and (mat["outline"] <= 0 or mat["blend"]):
                    continue
                tint = next((v for k, v in tints.items() if k in mat["name"]), None)
                p["tint"].value = tuple(c / 255 for c in tint[0]) if tint else (1, 1, 1)
                p["tint_curve"].value = tint[1] if tint else (0, 1)
                p["tint_amount"].value = 1.0 if tint else 0.0
                p["cut_dark"].value = tint[2] if tint and len(tint) > 2 and isinstance(tint[2], float) else -1.0
                pat = tint[2] if tint and len(tint) > 2 and isinstance(tint[2], dict) else None
                p["pat_on"].value = 1 if pat else 0
                if pat:   # {"a": 底色, "b": 交叉色, "line": 细线色, "scale": 格子密度}
                    p["pat_a"].value = tuple(c / 255 for c in pat["a"])
                    p["pat_b"].value = tuple(c / 255 for c in pat["b"])
                    p["pat_line"].value = tuple(c / 255 for c in pat["line"])
                    p["pat_scale"].value = pat["scale"]
                stock = stockings if (stockings and "Body_00_SKIN" in mat["name"]) else None
                p["stock_on"].value = 1 if stock else 0
                if stock:   # (颜色, 透明度：越小越透, 袜口高度：离地多少米)
                    p["stock_color"].value = tuple(c / 255 for c in stock[0])
                    p["stock_density"].value = stock[1]
                    p["stock_top"].value = stock[2]
                    p["root_y"].value = root_y
                p["outline"].value = mat["outline"] if pass_outline else 0.0
                p["is_outline"].value = 1 if pass_outline else 0
                p["color"].value = mat["color"]
                p["shade"].value = mat["shade"]
                p["shade_shift"].value = mat["shade_shift"]
                p["toony"].value = mat["toony"]
                p["cutoff"].value = mat["cutoff"] if not mat["blend"] else 0.02
                if mat["tex"]:
                    mat["tex"].use(0)
                    p["tex"].value = 0
                    p["has_tex"].value = 1
                else:
                    p["has_tex"].value = 0
                if pass_outline:
                    ctx.enable(moderngl.CULL_FACE); ctx.cull_face = "front"
                elif mat["cull"] == 0:
                    ctx.disable(moderngl.CULL_FACE)
                else:
                    ctx.enable(moderngl.CULL_FACE); ctx.cull_face = "back"
                if mat["blend"] and not pass_outline:
                    ctx.enable(moderngl.BLEND)
                    ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA, moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA
                    ctx.depth_mask = mat["zwrite"]
                else:
                    ctx.disable(moderngl.BLEND)
                    ctx.depth_mask = True
                pr["vao"].render()
        ctx.depth_mask = True
        ctx.disable(moderngl.BLEND)


# ======================================================================= 银月：姿态 / 表情 / 换色

# 换装：借 models/vrm 里其他 CC0 模型的衣服（VRoid 模型骨骼名字统一，可以直接套在银月身上），可选重新配色
# tint：材质名里包含这个词 → (颜色, (底亮度, 随原亮度增益))
P = lambda src, mats, tint=None: dict(src=src + ".vrm", mats=mats, tint=tint or {})
WHITE_S = ((250, 250, 255), 0.55, 0.66)     # 白丝：(颜色, 透明度, 袜口高度)
BLACK_S = ((22, 22, 30), 0.42, 0.66)        # 黑丝
LBLUE, PINK, LAV = (182, 212, 252), (255, 196, 214), (226, 214, 252)
def TOP(color):        # 单穿短袖：把制服的马甲和衬衫染成同一种纯色，看起来就是一件上衣
    return (color, (0.93, 0.07))


def PLAID(a, b, line, scale=14.0):   # 格纹裙
    return ((255, 255, 255), (1.0, 0.0), dict(a=a, b=b, line=line, scale=scale))


VRM_OUTFITS = {
    # ---- 连衣裙 / 一件式
    "魔法少女": dict(src="AvatarSample_G.vrm", tint={}),
    "白魔法少女": dict(src="AvatarSample_G.vrm", tint={"Tops": ((250, 251, 255), (0.62, 0.42)), "Onepice": ((250, 251, 255), (0.62, 0.42))}),
    "粉色连衣裙": dict(parts=[P("AvatarSample_D", ["Shoes"]), P("Darkness_Shibu", ["Tops"], {"Tops": (PINK, (0.78, 0.32))})]),
    "白色连衣裙白丝": dict(parts=[P("AvatarSample_D", ["Shoes"]), P("Darkness_Shibu", ["Tops"], {"Tops": ((252, 252, 255), (0.92, 0.12))})],
                       stockings=WHITE_S),
    "黑色连衣裙黑丝": dict(parts=[P("AvatarSample_D", ["Shoes"]), P("Darkness_Shibu", ["Tops"], {"Tops": ((32, 30, 42), (0.22, 0.6))})],
                       stockings=BLACK_S),
    # ---- 短袖 + 短裙（单穿一件上衣）
    "浅蓝短袖短裙白丝": dict(src="AvatarSample_D.vrm", tint={"Tops": TOP(LBLUE), "Bottoms": (LBLUE, (0.55, 0.5)),
                                                         "Accessory": TOP(LBLUE)}, stockings=WHITE_S),
    "白短袖黑短裙黑丝": dict(src="AvatarSample_D.vrm", tint={"Tops": TOP((250, 250, 255)), "Bottoms": ((30, 30, 40), (0.25, 0.7)),
                                                         "Accessory": TOP((250, 250, 255))}, stockings=BLACK_S),
    "酒红短袖黑丝": dict(src="AvatarSample_D.vrm", tint={"Tops": TOP((150, 34, 50)), "Bottoms": ((40, 22, 30), (0.25, 0.7)),
                                                     "Accessory": TOP((150, 34, 50))}, stockings=BLACK_S),
    "运动外套黑短裙黑丝": dict(parts=[P("AvatarSample_D", ["Bottoms", "Shoes"], {"Bottoms": ((30, 30, 40), (0.25, 0.7))}),
                                     P("AvatarSample_C", ["Tops"])], stockings=BLACK_S),
    # ---- 韩系：简洁纯色上衣 + 格纹百褶裙
    "韩系灰格裙": dict(src="AvatarSample_D.vrm", tint={"Tops": TOP((250, 250, 255)), "Accessory": TOP((250, 250, 255)),
                                                   "Bottoms": PLAID((120, 124, 136), (170, 174, 186), (40, 42, 55))}),
    "韩系奶咖格裙白丝": dict(src="AvatarSample_D.vrm", tint={"Tops": TOP((250, 244, 232)), "Accessory": TOP((250, 244, 232)),
                                                         "Bottoms": PLAID((196, 168, 132), (226, 206, 176), (110, 84, 60))},
                         stockings=WHITE_S),
    "韩系粉格裙": dict(src="AvatarSample_D.vrm", tint={"Tops": TOP((255, 236, 242)), "Accessory": TOP((255, 236, 242)),
                                                   "Bottoms": PLAID((240, 170, 190), (252, 214, 224), (200, 110, 140))}),
    "韩系黑白格裙黑丝": dict(src="AvatarSample_D.vrm", tint={"Tops": TOP((30, 30, 38)), "Accessory": TOP((30, 30, 38)),
                                                         "Bottoms": PLAID((30, 30, 38), (210, 210, 220), (250, 250, 255), 16.0)},
                         stockings=BLACK_S),
    "韩系蓝格裙": dict(src="AvatarSample_D.vrm", tint={"Tops": TOP((250, 250, 255)), "Accessory": TOP((250, 250, 255)),
                                                   "Bottoms": PLAID((70, 90, 150), (130, 150, 205), (240, 240, 250))}),
}
DEFAULT_VRM_OUTFIT = "浅蓝短袖短裙白丝"
HAIR_SRC = "Sendagaya_Shino.vrm"   # 发型：借 Sendagaya_Shino 的黑长直齐刘海（CC0），染成白色
HAIR_TINT = (236, 240, 252)        # 银白
IRIS_TINT = (150, 132, 245)        # 蓝紫
_state = {}


def _setup(w, h):
    r = a3._renderer(w, h)        # 和 3D 小狼共用一个显卡上下文
    if not _state:
        _state.update(r=r, vrm=VRM(r.ctx), wardrobe={})
    return _state


def _part_model(st, src, part):
    """按需加载别的模型（每个只加载一次），返回 (模型, 它名字里含 part 的网格，比如 Body 衣服、Hair 头发)。"""
    if src not in st["wardrobe"]:
        st["wardrobe"][src] = VRM(st["r"].ctx, MODEL.parent / src)
    m = st["wardrobe"][src]
    if part == "Hair":  # VRoid 把头皮那层底发放在身体网格里，要一起拿过来
        return m, [q for q in m.prims if "Hair" in q["mesh_name"] or "HAIR" in q["mat"]["name"]]
    return m, [q for q in m.prims if part in q["mesh_name"] and "HAIR" not in q["mat"]["name"]]


def _chest_lock(q):
    """垂到胸前的发束：在脸前方（VRoid 0.x 模型前方是 -Z）而且一直垂到肩膀以下。"""
    if "chest" not in q:
        pts = q["pos"][np.unique(q["idx"])]
        q["chest"] = bool(pts[:, 2].mean() < -0.02 and pts[:, 1].min() < 1.35)
    return q["chest"]


def _cat_ears(vrm):
    """原模型（HairSample_Female）头发里的猫耳：位置在头顶以上的几小块（约 6 块，每块几十个顶点）。"""
    if not hasattr(vrm, "_ears"):
        vrm._ears = []
        for q in vrm.prims:
            if "Hair" in q["mesh_name"]:
                pts = q["pos"][np.unique(q["idx"])]
                if pts[:, 1].min() > 1.5 and len(pts) < 60:
                    vrm._ears.append(q)
    return vrm._ears


def _outfit_model(st, name):
    return _part_model(st, VRM_OUTFITS[name]["src"], "Body")  # 只借衣服和身体，不带原模型的底发


def _outfit_pieces(st, name):
    """一套衣服由哪些部件组成：[(模型, 网格列表, 配色)]。
    简单写法 {"src": 模型, "tint": 配色}：整套借一个模型。
    混搭写法 {"parts": [{"src": 模型, "mats": ["Tops"], "tint": {...}}, ...]}：上衣、下装、鞋子分别从不同模型拿，
    身体皮肤用第一个部件所在的模型。"""
    o = VRM_OUTFITS[name]
    if "parts" not in o:
        m, prims = _outfit_model(st, name)
        return [(m, prims, o.get("tint", {}))]
    out = []
    for i, part in enumerate(o["parts"]):
        m, prims = _part_model(st, part["src"], "Body")
        keys = part["mats"] + (["SKIN"] if i == 0 else [])
        out.append((m, [q for q in prims if any(k in q["mat"]["name"] for k in keys)], part.get("tint", {})))
    return out


def _walk_leg(vrm: VRM, rotations, side: str, reach: float, lift: float, drop: float, lateral: float = 0):
    """Solve the two leg joints to put the ankle on the planned footprint."""
    upper, lower, foot = (vrm.bone[side + part] for part in ("UpperLeg", "LowerLeg", "Foot"))
    y1, z1 = vrm.rest[lower][1:3, 3]
    y2, z2 = vrm.rest[foot][1:3, 3]
    thigh, shin = math.hypot(y1, z1), math.hypot(y2, z2)
    forward = float(-z1 - z2 + reach)
    down = float(-y1 - y2 - drop - lift)
    roll = math.degrees(math.atan2(lateral, down))
    down = math.hypot(down, lateral)
    length = math.hypot(forward, down)
    span = min(thigh + shin - 1e-5, length)
    if span < length:
        forward, down = forward * span / length, down * span / length
    knee_offset = math.acos(max(-1.0, min(1.0, (thigh**2 + span**2 - shin**2) / (2 * thigh * span))))
    thigh_angle = math.atan2(forward, down) + knee_offset
    shin_angle = math.atan2(forward - thigh * math.sin(thigh_angle), down - thigh * math.cos(thigh_angle))
    thigh_delta = math.degrees(thigh_angle - math.atan2(-z1, -y1))
    shin_delta = math.degrees(shin_angle - math.atan2(-z2, -y2))
    rotations[upper] = a3.RZ(roll) @ _euler(x=thigh_delta)
    rotations[lower] = _euler(x=shin_delta - thigh_delta)
    rotations[foot] = _euler(x=-shin_delta) @ a3.RZ(-roll)


def _pose(vrm: VRM, p: dict):
    """把形象的姿态参数换成骨骼旋转。模型静止时是 T 字站姿（双臂平举）。"""
    b = vrm.bone
    rot = {}
    t = p.get("phase", 0)
    breathe = math.sin(t * 2.2) * 1.2
    rot[b["spine"]] = _euler(x=breathe * 0.6)
    rot[b["chest"]] = _euler(x=breathe * 0.4)
    tilt, look = p.get("tilt", 0), p.get("look", 0)
    rot[b["neck"]] = _euler(y=look * 12, z=tilt * 0.4)
    rot[b["head"]] = _euler(x=-3, y=look * 10, z=tilt * 0.8)
    if reach := p.get("reach", 0.0):
        rot[b["spine"]] = _euler(x=-4 * reach)
        rot[b["chest"]] = _euler(x=-6 * reach)
        rot[b["head"]] = _euler(x=-8 * reach)
    if bow := p.get("bow", 0.0):
        rot[b["hips"]] = _euler(x=-8 * bow)
        rot[b["spine"]] = _euler(x=-15 * bow)
        rot[b["chest"]] = _euler(x=-10 * bow)
        rot[b["neck"]] = _euler(x=-5 * bow)
        rot[b["head"]] = _euler(x=5 * bow)
    peek = p.get("peek", 0.0)
    if peek:
        rot[b["hips"]] = _euler(z=13 * peek)
        rot[b["spine"]] = _euler(z=19 * peek)
        rot[b["chest"]] = _euler(z=16 * peek)
        rot[b["neck"]] = _euler(y=look * 12, z=9 * peek)
        rot[b["head"]] = _euler(x=-3, y=look * 10, z=-27 * peek)
        for side in ("left", "right"):
            rot[b[f"{side}UpperLeg"]] = _euler(z=13 * peek)
            rot[b[f"{side}LowerLeg"]] = _euler(z=-4 * peek)
            rot[b[f"{side}Foot"]] = _euler(z=-9 * peek)
    if bend := p.get("peek_bend", 0):
        rot[b["spine"]] = _euler(x=10 * bend)
        rot[b["chest"]] = _euler(x=6 * bend)
        rot[b["head"]] = _euler(x=-15 * bend)

    arm = p.get("arm", "down")
    down, elbow = 72 + 14 * abs(peek), 12    # 探头时手臂收在身侧
    left = dict(z=down, x=6)
    right = dict(z=-down, x=6)
    if arm == "wave":
        right = dict(z=-down + 150 + p.get("wave", 0) * 0.8, x=-10)
        rot[b["rightLowerArm"]] = _euler(z=40 + p.get("wave", 0))
    elif arm == "up":
        left, right = dict(z=-60, x=-5), dict(z=60, x=-5)
    elif raise_arm := p.get("wave_raise", 0.0):
        wave = p.get("wave", 0.0)
        right = dict(z=-down + 121 * raise_arm, x=6 - 12 * raise_arm)
        rot[b["rightLowerArm"]] = _euler(y=elbow * (1 - raise_arm), z=36 * raise_arm + wave)
    arms = p.get("arms")   # 舞蹈用：{"left": (前后x, 抬起z, 弯肘), "right": (...)}，z=0 是平举，正数往下放
    if arms:
        lx, lz, le = arms["left"]
        rx, rz, re_ = arms["right"]
        left, right = dict(x=lx, z=lz), dict(x=rx, z=-rz)
        rot[b["leftLowerArm"]] = _euler(y=-le)
        rot[b["rightLowerArm"]] = _euler(y=re_)
    rot[b["leftUpperArm"]] = _euler(x=left["x"], z=left["z"])
    rot[b["rightUpperArm"]] = _euler(x=right["x"], z=right["z"])
    rot.setdefault(b["leftLowerArm"], _euler(y=-elbow))
    rot.setdefault(b["rightLowerArm"], _euler(y=elbow))
    if "walk_feet" in p:
        for side, (reach, lift) in zip(("left", "right"), p["walk_feet"]):
            _walk_leg(vrm, rot, side, reach, lift, p["walk_drop"])
    if "dance_feet" in p:
        lean = p["dance_lean"]
        rot[b["spine"]] = _euler(x=-0.35 * abs(lean), z=lean * 0.62)
        rot[b["chest"]] = _euler(y=lean * 0.7, z=lean * 0.30)
        rot[b["head"]] = _euler(x=-3, y=-lean * 0.55, z=-lean * 0.48)
        for side, (lateral, reach, lift) in zip(("left", "right"), p["dance_feet"]):
            _walk_leg(vrm, rot, side, reach, lift, p["walk_drop"], lateral)
    sway, knee = p.get("hip_sway", 0.0), p.get("knee", 0.0)   # 扭胯（度）、屈膝（0~1）
    groove = p.get("groove")                                   # 舞蹈节拍位置（拍数）：有它就让全身跟着律动
    if sway or knee or groove is not None:
        g = groove or 0.0
        ph = 2 * math.pi * g
        twist = 9 * math.sin(ph / 2) if groove is not None else 0.0        # 胸口每两拍左右扭一次
        lean = 5 * math.sin(ph / 2) if groove is not None else 0.0          # 上身跟着重心左右倾
        nod = 7 * max(0.0, math.cos(ph)) if groove is not None else 0.0     # 每拍点一下头
        step = math.sin(ph / 2) if groove is not None else 0.0              # 左右脚交替踩步
        rot[b["hips"]] = _euler(z=sway - lean * 0.6, y=sway * 0.6 - twist * 0.4)
        rot[b["spine"]] = _euler(x=breathe * 0.6, z=-sway * 0.7 + lean, y=twist * 0.5)
        rot[b["chest"]] = _euler(x=breathe * 0.4, y=twist * 0.6)
        rot[b["neck"]] = _euler(y=look * 12, z=tilt * 0.4, x=nod * 0.4)
        rot[b["head"]] = _euler(x=-3 + nod, y=look * 10 - twist * 0.3, z=tilt * 0.8)
        for side, sgn in (("left", 1), ("right", -1)):
            lift = max(0.0, sgn * step) if groove is not None else 0.0   # 这条腿抬起来踩一下
            rot[b[f"{side}UpperLeg"]] = _euler(x=-28 * knee - 22 * lift)
            rot[b[f"{side}LowerLeg"]] = _euler(x=50 * knee + 38 * lift)
            rot[b[f"{side}Foot"]] = _euler(x=-22 * knee - 10 * lift)
            if groove is not None and f"{side}Shoulder" in b:          # 肩膀跟着拍子上下颠
                rot[b[f"{side}Shoulder"]] = _euler(z=sgn * 5 * math.sin(ph))
    # 手指微微弯，看起来自然
    for side in ("left", "right"):
        for f in ("Index", "Middle", "Ring", "Little"):
            for seg in ("Proximal", "Intermediate"):
                key = f"{side}{f}{seg}"
                if key in b:
                    rot[b[key]] = _euler(z=(-18 if side == "left" else 18))
    return rot


def _expression(p: dict) -> dict:
    eyes, mouth = p.get("eyes", "open"), p.get("mouth", "smile")
    w = {}
    if eyes in ("closed", "sleep"):
        w["blink"] = 1.0
    elif eyes == "happy":
        w["joy"] = 1.0
    elif eyes == "wink":
        w["blink_l"] = 1.0
        w["fun"] = 0.4
    # 平时保持自然睁眼（这个模型的 fun/joy 都会眯眼）
    if mouth == "open":
        w["a"] = 0.7
    elif mouth == "yawn":
        w["o"] = 1.0
    elif mouth == "o":
        w["o"] = 0.5
    return w


_DANCE_BLEND = 0.38
_DANCE_FILE = MODEL.parents[1] / "motion" / "modern_dance.npz"
if _DANCE_FILE.is_file():
    with np.load(_DANCE_FILE, allow_pickle=False) as _clip:
        _DANCE = {key: _clip[key] for key in ("fps", "names", "rotations", "roots")}
    DANCE_DURATION = len(_DANCE["roots"]) / int(_DANCE["fps"]) + _DANCE_BLEND * 2
else:
    DANCE_DURATION = 0.0


def _nlerp(a, b, u):
    if np.dot(a, b) < 0:
        b = -b
    q = a * (1 - u) + b * u
    return q / np.linalg.norm(q)


def _dance_pose(rig, base_rot, t, strength=1.0):
    """Sample the recorded joint rotations and blend the entry/exit with the idle pose."""
    u = max(0., min(1., t / _DANCE_BLEND, (DANCE_DURATION - t) / _DANCE_BLEND))
    weight = u * u * (3 - 2 * u) * strength
    frame = np.clip((t - _DANCE_BLEND) * int(_DANCE["fps"]), 0, len(_DANCE["roots"]) - 1)
    lo = int(frame)
    hi = min(lo + 1, len(_DANCE["roots"]) - 1)
    frac = frame - lo
    offset = (_DANCE["roots"][lo] * (1 - frac) + _DANCE["roots"][hi] * frac) * weight
    for name, a, b in zip(_DANCE["names"], _DANCE["rotations"][lo], _DANCE["rotations"][hi]):
        idx = rig.bone[name]
        q = _nlerp(a, b, frac)
        if weight < 1.0:
            rest = base_rot.get(idx)
            q = _nlerp(_mat_quat(rest[:3, :3]) if rest is not None else np.array([0, 0, 0, 1]), q, weight)
        base_rot[idx] = _quat_mat(q)
    return offset


def draw_yinyue_vrm(p: dict, outfits: dict, default_outfit: str, w: int, h: int) -> Image.Image:
    st = _setup(w, h)
    r, vrm = st["r"], st["vrm"]
    name = p.get("outfit") if p.get("outfit") in VRM_OUTFITS else DEFAULT_VRM_OUTFIT
    ctx = r.ctx
    r.fbo.use()
    ctx.clear(0, 0, 0, 0)
    ctx.enable(moderngl.DEPTH_TEST)

    yaw = 180 + 15 + p.get("spin", 0)        # VRoid 0.x 模型面朝 -Z，转过来再微微侧身
    gb = p.get("groove")
    bounce = 0.018 * (1 - math.cos(2 * math.pi * gb)) / 2 if gb is not None else 0.0   # 每拍往下颠一下
    shift = 0.03 * math.sin(math.pi * gb) if gb is not None else 0.0                 # 重心左右移
    root = a3.M(a3.T(p.get("dx", 0) * 0.012 + shift,
                     p.get("bob", 0) * 0.003 + p.get("jump", 0) * 0.012 - p.get("knee", 0) * 0.06 - bounce - p.get("walk_drop", 0), 0),
                a3.RY(yaw))
    rotations = _pose(vrm, p)
    floor_lift = None
    if DANCE_DURATION and "mocap_time" in p:
        offset = _dance_pose(vrm, rotations, p["mocap_time"], p.get("mocap_strength", 1.0))
        root = a3.RY(yaw) @ a3.T(offset[0], 0, offset[2])
        floor_lift = offset[1]
    elif "dance_feet" in p:
        root = a3.T(-p["dance_shift"], -p["walk_drop"], -p.get("dance_depth", 0)) @ a3.RY(yaw)
        floor_lift = min(foot[2] for foot in p["dance_feet"])
    world = vrm.pose_matrices(rotations, root)
    if floor_lift is not None:
        if not hasattr(vrm, "dance_floor"):
            vrm.dance_feet = [vrm.bone[key] for key in ("leftFoot", "rightFoot", "leftToes", "rightToes") if key in vrm.bone]
            standing = vrm.pose_matrices({}, np.eye(4, dtype="f4"))
            vrm.dance_floor = min(standing[i][1, 3] for i in vrm.dance_feet)
        lowest = min(world[i][1, 3] for i in vrm.dance_feet)
        dy = vrm.dance_floor + floor_lift - lowest
        for matrix in world:
            matrix[1, 3] += dy
        root[1, 3] += dy
    vrm.set_expressions(_expression(p))

    eye, target = (0, 1.09, 4.08), (0, 0.91, 0)
    projection = a3._perspective(28, w / h, 0.05, 20)
    # Extra canvas captures wide gestures while preserving the resting focal length in pixels.
    projection[:2] *= p.get("viewport_height", h) / h
    vp = projection @ a3._look_at(eye, target)
    light = (0.4, 0.8, 0.9)
    tints = {"HAIR": (HAIR_TINT, (0.74, 1.15)), "EyeIris": (IRIS_TINT, (0.35, 1.2))}
    vrm.draw(world, vp, eye, light, tints, prims=[q for q in vrm.prims if "Face" in q["mesh_name"]])   # 脸
    hair_model, hair = _part_model(st, p.get("hair_src", HAIR_SRC), "Hair")
    hair = [q for q in hair if not _chest_lock(q)]                                                      # 不要垂到胸前的发束
    hair_model.draw(world, vp, eye, light, tints, prims=hair, rig=vrm)                                  # 长直发
    vrm.draw(world, vp, eye, light, tints, prims=_cat_ears(vrm))                                        # 保留原模型的猫耳
    if not p.get("head_only"):
        stock = VRM_OUTFITS[name].get("stockings")
        for src, body, tint in _outfit_pieces(st, name):                                               # 借来的身体+衣服
            src.draw(world, vp, eye, light, tint, prims=body, rig=vrm, stockings=stock, root_y=float(root[1, 3]))

    # 头皮垫层：借来的发型配原来的脸，发缝里会露出脸部贴图上的浅色头皮，垫一层银白盖住（只留上半球）
    head = world[vrm.bone["head"]]
    r.add("sphere", head @ a3.T(0, 0.085, -0.008) @ a3.S(0.083, 0.088, 0.09), a3.HAIR, clip=(0, 1, 0, 0.12),
          outline=False, flat=0.3)

    # 狐尾：一整条平滑蓬松的尾巴挂在臀部骨骼上，随动作左右甩（avatar3d 里的 tail_body / tail_tip 网格）
    if not p.get("head_only"):
        hips = world[vrm.bone["hips"]]
        tail = hips @ a3.T(-0.035, -0.04, 0.09) @ a3.RY(-69 + p.get("tail", 0) * 1.4) @ a3.S(0.68, 0.65, 0.69)  # 模型自己的 +Z 是身后
        r.add("tail_body", tail, a3.HAIR, outline=False, flat=0.15)   # 不描边，留一点体积光影
        r.add("tail_tip", tail, (255, 255, 255), outline=False, flat=0.3)
    img = r.render_parts(vp, eye)
    return a3._extras(img, p, w, h)
