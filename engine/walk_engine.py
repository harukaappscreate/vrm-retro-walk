"""walk_engine - Headless Pyxel 3 VRM renderer & binary IPC worker for VRM RETRO WALK v3.

Features:
- Intro Giant Reveal -> jitter-free smooth downscale landing (1.60s) inside big transparent frame
- Dual Sit support (floor-sit vs window-edge perch sit with dangling legs)
- Scale-aware walking & resize surprise expression
- Bulletproof parent-PID orphan self-termination
"""
# SPDX-License-Identifier: MIT
import json
import math
import os
import random
import struct
import sys
import tempfile
import threading
import time

_IS_IPC = "--ipc" in sys.argv
_PROTO_OUT = None
if _IS_IPC:
    _proto_fd = os.dup(1)
    os.dup2(2, 1)
    if os.name == "nt":
        import msvcrt
        msvcrt.setmode(_proto_fd, os.O_BINARY)
        try:
            msvcrt.setmode(0, os.O_BINARY)
        except OSError:
            pass
    _PROTO_OUT = os.fdopen(_proto_fd, "wb", buffering=0)
    sys.stdout = sys.stderr

import numpy as np
import pyxel
from pyxel.cube import Camera, Mat4, Node, Primitive, Shading, Vec3

import procedural_rig as PR
import vrm_io as V

W, H = 144, 164
KEY = 255
UI = [
    0x161224, 0x241F36, 0x2A324D, 0x19959C,
    0x8B4852, 0x395C98, 0xA9C1FF, 0xFFFFFF,
    0xFF5C8A, 0xD38441, 0xFFE27A, 0x70C6A9,
    0x7696DE, 0x8F9AC0, 0xFF9798, 0xEDC7B0,
]

GB_PALETTE = [0x0F380F, 0x306230, 0x8BAC0F, 0x9BBC0F]
AMBER_PALETTE = [0x120800, 0x3A1A00, 0x703800, 0xA85808, 0xD87E10, 0xFFB020, 0xFFD470]
HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="vrmwalk3_")
INTRO_DUR = 1.60


def remap_palette(base_cols, style):
    if style == "CRISP":
        return list(base_cols)
    shades = GB_PALETTE if style == "GB" else AMBER_PALETTE
    n_sh = len(shades)
    out = []
    for i, c in enumerate(base_cols):
        if i == KEY:
            out.append(0xFF00FF)
        elif i == 0:
            out.append(shades[0])
        else:
            r, g, b = (c >> 16) & 255, (c >> 8) & 255, c & 255
            lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
            out.append(shades[min(n_sh - 1, max(0, int(lum * (n_sh - 0.001))))])
    return out


def png_from_index(idx, palette, path):
    from PIL import Image
    cols = np.zeros((256, 3), np.uint8)
    cols[:16] = [((c >> 16) & 255, (c >> 8) & 255, c & 255) for c in UI]
    cols[16:16 + len(palette)] = palette
    cols[KEY] = (255, 0, 255)
    Image.fromarray(cols[idx]).save(path)


def apply_mosaic_reveal(pix_bytes, block):
    if block <= 1:
        return pix_bytes
    arr = np.frombuffer(pix_bytes, dtype=np.uint8).reshape(H, W)
    bh, bw = H // block, W // block
    sub = arr[:bh * block:block, :bw * block:block]
    up = np.repeat(np.repeat(sub, block, axis=0), block, axis=1)
    if up.shape != arr.shape:
        out = np.full_like(arr, KEY)
        out[:up.shape[0], :up.shape[1]] = up
        return out.tobytes()
    return up.tobytes()


def downscale_frame_bottom_center(pix_bytes, scale, hop_y=0):
    """Jitter-free bottom-centered nearest downscale inside the 144x164 buffer for smooth intro shrink."""
    s = max(0.25, min(1.0, float(scale)))
    if s >= 0.995 and hop_y == 0:
        return pix_bytes
    src = np.frombuffer(pix_bytes, dtype=np.uint8).reshape(H, W)
    nw = max(16, int(round(W * s)))
    nh = max(20, int(round(H * s)))
    yi = (np.arange(nh) * H) // nh
    xi = (np.arange(nw) * W) // nw
    scaled = src[yi[:, None], xi[None, :]]
    out = np.full((H, W), KEY, dtype=np.uint8)
    x0 = (W - nw) // 2
    y0 = max(0, H - nh - int(hop_y))
    y1 = min(H, y0 + nh)
    x1 = min(W, x0 + nw)
    out[y0:y1, x0:x1] = scaled[:(y1 - y0), :(x1 - x0)]
    return out.tobytes()


class MeshFigureNode(Node):
    def __init__(self, rig):
        super().__init__()
        self.rig = rig
        self.prims = []
        for p in rig.m.parts:
            pr = Primitive(
                Primitive.MODE_TRIANGLES,
                p["pos"].reshape(-1).tolist(),
                p["idx"].astype(np.int64).tolist(),
                [],
                p["uv"].reshape(-1).tolist(),
                cull=Primitive.CULL_NONE if p["double"] else Primitive.CULL_BACK,
            )
            pr.compute_normals()
            self.prims.append(pr)
        self.textures = []
        for k, idx in enumerate(rig.m.tex_index):
            path = os.path.join(TMP, f"tex_{k}.png")
            png_from_index(idx, rig.m.palette, path)
            self.textures.append(pyxel.Image.from_image(path))
        self.draw_order = sorted(
            range(len(rig.m.parts)),
            key=lambda i: {"OPAQUE": 0, "MASK": 1}.get(rig.m.parts[i]["alpha"], 2),
        )
        self.identity = Mat4()

    def sync(self, skinned_parts):
        for pr, pos in zip(self.prims, skinned_parts):
            pr.positions[:] = pos.reshape(-1).tolist()
            pr.compute_normals()

    def on_draw(self):
        for i in self.draw_order:
            p = self.rig.m.parts[i]
            if p["keyed"]:
                self.prim(self.identity, self.prims[i], self.textures[p["tex"]], colkey=KEY)
            else:
                self.prim(self.identity, self.prims[i], self.textures[p["tex"]])


class WalkRenderer:
    def __init__(self, initial_vrm=None, sample_vrm=None):
        pyxel.init(W, H, title="VRM RETRO WALK", fps=30, headless=True)
        self.base_cols = list(UI) + [0x808080] * (255 - 16) + [0xFF00FF]
        self.active_cols = list(self.base_cols)
        pyxel.colors[:] = self.active_cols[:256]
        self.pal_ver = 1
        self.style = "CRISP"
        self.outline_on = True

        self.root = Node()
        self.cam = Camera()
        self.cam.fov = 34.0
        self.cam.near = 0.05
        self.cam.far = 30.0
        self.cam.clear_color = KEY
        self.root.camera = self.cam
        self.mesh_node = None

        self.brain = PR.MascotBrain()
        self.rng = random.Random(77)
        self.particles = []
        self._particle_cd = 0.0
        self.morph_fx_t = 0.0
        self.intro_t = -1.0   # >= 0 when playing giant intro reveal
        self.status_text = ""
        self.status_error = False
        # The built-in avatar (Shibu) and the avatar to show first are kept apart,
        # so that "back to the sample" always returns to the built-in one.
        self.sample_path = self._find_sample(sample_vrm)
        first = initial_vrm if (initial_vrm and os.path.exists(initial_vrm)) else self.sample_path

        if first:
            ok, msg = self.load_vrm(first, play_morph=True)
            if not ok and self.sample_path and first != self.sample_path:
                ok, msg = self.load_vrm(self.sample_path, play_morph=True)
            if not ok:
                self._install_model(PR.ProceduralModel())
                self.status_text, self.status_error = msg, True
        else:
            self._install_model(PR.ProceduralModel())

    def _find_sample(self, explicit_path):
        for p in (
            explicit_path,
            os.path.join(os.path.dirname(HERE), "assets", "sample.vrm"),
            os.path.join(HERE, "sample.vrm"),
        ):
            if p and os.path.exists(p):
                return p
        return None

    def apply_style(self, style=None, outline=None):
        changed = False
        if style in ("CRISP", "GB", "AMBER") and style != self.style:
            self.style = style
            changed = True
        if outline is not None and bool(outline) != self.outline_on:
            self.outline_on = bool(outline)
            if self.mesh_node is not None:
                self.mesh_node.outline = 0 if self.outline_on else None
        if changed or self.mesh_node is None:
            cols = remap_palette(self.base_cols, self.style)
            cols[KEY] = 0xFF00FF
            self.active_cols = cols[:256]
            pyxel.colors[:] = self.active_cols
            sh = Shading(self.active_cols[:255])
            sh.direction = Vec3(-0.32, -0.78, -0.54)
            self.root.shading = sh
            self.pal_ver += 1

    def _install_model(self, model):
        self.base_cols = list(UI) + [int(r) << 16 | int(g) << 8 | int(b) for r, g, b in model.palette]
        self.base_cols += [0x808080] * (255 - len(self.base_cols))
        self.base_cols.append(0xFF00FF)
        self.base_cols = self.base_cols[:256]

        self.rig = PR.RigState(model)
        if self.mesh_node is not None:
            self.root.remove_child(self.mesh_node)
        self.mesh_node = MeshFigureNode(self.rig)
        self.mesh_node.outline = 0 if self.outline_on else None
        self.root.add_child(self.mesh_node)
        self.apply_style(self.style, self.outline_on)
        self.pal_ver += 1

        # Compute actual rest-pose bounding box across all mesh parts so chibi, tall, and VRM 0.x models all frame cleanly
        lo = np.min([p["pos"].min(0) for p in model.parts], 0) if model.parts else np.array([-0.3, 0.0, -0.2])
        hi = np.max([p["pos"].max(0) for p in model.parts], 0) if model.parts else np.array([0.3, 1.6, 0.2])
        lo_x, hi_x = float(lo[0]), float(hi[0])
        lo_y, hi_y = float(lo[1]), float(hi[1])
        span_x = max(0.35, hi_x - lo_x)
        span_y = max(0.45, hi_y - lo_y)
        dist = max(span_y * 2.22, span_x * 2.45)
        tgt_y = lo_y + span_y * 0.49
        hips = model.human.get("hips")
        hip_y = float(model.rest_world[hips][1, 3]) if hips is not None else (lo_y + span_y * 0.55)
        self.brain.floor_sit_scale = max(0.45, min(1.25, (hip_y - lo_y) / max(0.25, hip_y)))

        self.cam.transform = Mat4.look_at(
            Vec3(0.0, tgt_y + span_y * 0.05, dist),
            Vec3(0.0, tgt_y, 0.0),
            Vec3(0.0, 1.0, 0.0),
        )

    def start_intro(self):
        self.intro_t = 0.0
        self.morph_fx_t = 0.0
        self.brain.trigger_state("wave", INTRO_DUR + 0.4)
        self.brain.sfx_queue.append("load")
        for _ in range(9):
            self._spawn_particle(
                "sparkle",
                W * 0.5 + self.rng.uniform(-34, 34),
                self.rng.uniform(32, 134),
            )

    def load_vrm(self, path, play_morph=True):
        try:
            with open(path, "rb") as f:
                data = f.read()
            j, _ = V.read_glb(data)
            allowed, lic_msg = V.modification_allowed(j)
            if not allowed:
                return False, f"License blocked ({lic_msg})"
            model = V.VrmModel(data)
            self._install_model(model)
            self.brain.trigger_state("wave", 2.0)
            self.brain.sfx_queue.append("load")
            if play_morph:
                self.morph_fx_t = 0.70
                for _ in range(7):
                    self._spawn_particle(
                        "sparkle",
                        W * 0.5 + self.rng.uniform(-24, 24),
                        self.rng.uniform(44, 128),
                    )
            self.status_text = f"Loaded {model.title}"
            self.status_error = False
            return True, self.status_text
        except Exception as e:
            self.status_text = f"Load failed: {e}"
            self.status_error = True
            return False, self.status_text

    def _spawn_particle(self, kind, x, y):
        vx = self.rng.uniform(-12.0, 12.0)
        vy = self.rng.uniform(-26.0, -14.0)
        self.particles.append([kind, float(x), float(y), vx, vy, 0.85, 0.85])

    def _update_particles(self, dt, eff_state, on_window_edge):
        self._particle_cd = max(0.0, self._particle_cd - dt)
        if eff_state == "pet" and self._particle_cd <= 0.0:
            self._spawn_particle("heart", W * 0.5 + self.rng.uniform(-16, 16), 34)
            self._particle_cd = 0.28
        elif eff_state == "nap" and self._particle_cd <= 0.0:
            zy = 50 if on_window_edge else 74
            self._spawn_particle("z", W * 0.5 + 16, zy)
            self._particle_cd = 0.72

        alive = []
        for p in self.particles:
            p[1] += p[3] * dt
            p[2] += p[4] * dt
            p[5] -= dt
            if p[5] > 0.0:
                alive.append(p)
        self.particles = alive

    def _draw_shadow(self, airborne, eff_state, on_window_edge):
        if airborne:
            return
        if on_window_edge and eff_state in ("sit", "nap"):
            return
        is_sit = eff_state in ("sit", "nap")
        rx = 26 if is_sit else 16
        ry = 4 if on_window_edge else (5 if is_sit else 4)
        cy = H - 18
        cx = (W // 2 + self.brain.facing * 4) if is_sit else (W // 2)
        for dy in range(-ry, ry + 1):
            for dx in range(-rx, rx + 1):
                if (dx * dx) / (rx * rx) + (dy * dy) / (ry * ry) <= 1.0:
                    px, py = cx + dx, cy + dy
                    if 0 <= px < W and 0 <= py < H and ((dx + dy) & 1) == 0 and pyxel.pget(px, py) == KEY:
                        pyxel.pset(px, py, 1)

    def _draw_particles_to_buf(self, pix_bytes):
        if not self.particles:
            return pix_bytes
        arr = np.frombuffer(pix_bytes, dtype=np.uint8).reshape(H, W).copy()
        for kind, x, y, _, _, life, max_life in self.particles:
            frac = life / max_life
            ix, iy = int(x), int(y)
            if kind == "heart":
                col = 8 if frac > 0.35 else 14
                for dx, dy in ((-2,-1),(-1,-1),(1,-1),(2,-1),(-2,0),(-1,0),(0,0),(1,0),(2,0),(-1,1),(0,1),(1,1),(0,2)):
                    if 0 <= ix + dx < W and 0 <= iy + dy < H:
                        arr[iy + dy, ix + dx] = col
            elif kind == "z":
                col = 6
                pts = ((0,0),(1,0),(2,0),(3,0),(4,0),(3,1),(2,2),(1,3),(0,4),(1,4),(2,4),(3,4),(4,4)) if frac > 0.5 else ((0,0),(1,0),(2,0),(1,1),(0,2),(1,2),(2,2))
                for dx, dy in pts:
                    if 0 <= ix + dx < W and 0 <= iy + dy < H:
                        arr[iy + dy, ix + dx] = col
            elif kind == "sparkle":
                col = 10 if frac > 0.4 else 7
                for dx, dy in ((0,0),(-2,0),(2,0),(0,-2),(0,2),(-1,0),(1,0),(0,-1),(0,1)):
                    if 0 <= ix + dx < W and 0 <= iy + dy < H:
                        arr[iy + dy, ix + dx] = col
        return arr.tobytes()

    def render_step(self, req):
        cmd = req.get("cmd", "step")
        if cmd == "load_vrm" and req.get("path"):
            ok, _ = self.load_vrm(req["path"], play_morph=True)
            if ok and req.get("intro_big"):
                self.start_intro()
        elif cmd == "reset_sample" and self.sample_path:
            ok, _ = self.load_vrm(self.sample_path, play_morph=True)
            if ok and req.get("intro_big"):
                self.start_intro()
        elif cmd == "replay_intro":
            self.start_intro()

        if "style" in req or "outline" in req:
            self.apply_style(req.get("style"), req.get("outline"))

        dt = float(req.get("dt", 1.0 / 30.0))
        intro_active = bool(req.get("intro_big", False))
        if intro_active and self.intro_t < 0.0:
            self.start_intro()

        on_edge = bool(req.get("on_window_edge", False))
        eff_state, vx = self.brain.step(
            dt,
            getattr(self.rig.m, "available_expressions", []),
            mx=float(req.get("mx", 0.0)),
            my=float(req.get("my", 0.0)),
            hover=bool(req.get("hover", False)),
            pet=bool(req.get("pet", False)),
            poke=bool(req.get("poke", False)),
            dblclick=bool(req.get("dblclick", False)),
            grabbing=bool(req.get("grabbing", False)),
            drag_vx=float(req.get("drag_vx", 0.0)),
            airborne=bool(req.get("airborne", False)),
            just_landed=bool(req.get("just_landed", False)),
            just_perched=bool(req.get("just_perched", False)),
            on_window_edge=on_edge,
            resizing=bool(req.get("resizing", False)),
            scale_factor=float(req.get("scale_factor", 1.0)),
            hit_edge=int(req.get("hit_edge", 0)),
            auto_mode=False if intro_active else bool(req.get("auto_mode", True)),
            force_state="wave" if (intro_active and self.intro_t < 1.05) else ("idle" if intro_active else str(req.get("force_state", ""))),
        )
        self._update_particles(dt, eff_state, on_edge)

        q, tr = self.rig.pose_from_brain(self.brain.cur_D, self.brain.cur_hp, self.brain.cur_yaw)
        skinned = self.rig.skin_parts(q, tr, self.brain.cur_expr)
        self.mesh_node.sync(skinned)

        self.root.draw(0, 0, W, H)
        self._draw_shadow(bool(req.get("airborne", False) or req.get("grabbing", False)), eff_state, on_edge)

        pix = bytes(pyxel.screen.data_ptr())
        intro_done = False

        if intro_active:
            self.intro_t += dt
            target_ratio = float(req.get("intro_target_ratio", 0.42))
            if self.intro_t < 0.14:
                pix = apply_mosaic_reveal(pix, 8)
            elif self.intro_t < 0.28:
                pix = apply_mosaic_reveal(pix, 4)
            elif self.intro_t < 0.42:
                pix = apply_mosaic_reveal(pix, 2)
            elif self.intro_t >= 1.05:
                u = min(1.0, (self.intro_t - 1.05) / max(0.05, INTRO_DUR - 1.05))
                ease = u * u * (3.0 - 2.0 * u)
                s_cur = 1.0 + (target_ratio - 1.0) * ease
                hop = math.sin(u * math.pi) * 14.0
                pix = downscale_frame_bottom_center(pix, s_cur, hop_y=hop)
            pix = self._draw_particles_to_buf(pix)
            if self.intro_t >= INTRO_DUR:
                intro_done = True
                self.intro_t = -1.0
                self.brain.trigger_state("walk", 3.6)
        else:
            if self.morph_fx_t > 0.0:
                self.morph_fx_t = max(0.0, self.morph_fx_t - dt)
                if self.morph_fx_t > 0.52:
                    pix = apply_mosaic_reveal(pix, 8)
                elif self.morph_fx_t > 0.36:
                    pix = apply_mosaic_reveal(pix, 4)
                elif self.morph_fx_t > 0.22:
                    pix = apply_mosaic_reveal(pix, 2)
            pix = self._draw_particles_to_buf(pix)

        sfx = self.brain.sfx_queue
        self.brain.sfx_queue = []
        m = self.rig.m
        meta = {
            "w": W,
            "h": H,
            "state": eff_state,
            "facing": self.brain.facing,
            "vx": 0.0 if intro_active else round(float(vx), 2),
            "sfx": sfx,
            "title": m.title,
            "version": m.version,
            "author": m.author,
            "tris": int(m.tris),
            "exprCount": len(getattr(m, "available_expressions", [])),
            "style": self.style,
            "outline": self.outline_on,
            "morphFx": (self.morph_fx_t > 0.0) or intro_active,
            "introDone": intro_done,
            "status": self.status_text,
            "statusError": self.status_error,
            "palVer": self.pal_ver,
        }
        last_sent_pal = int(req.get("last_pal_ver", 0))
        if last_sent_pal != self.pal_ver:
            meta["palette"] = self.active_cols

        self.status_text = ""
        self.status_error = False
        return meta, pix

    def _emit_packet(self, meta, pix):
        meta_bytes = json.dumps(meta, separators=(",", ":")).encode("utf-8")
        _PROTO_OUT.write(b"VRMW")
        _PROTO_OUT.write(struct.pack("<I", len(meta_bytes)))
        _PROTO_OUT.write(meta_bytes)
        _PROTO_OUT.write(struct.pack("<I", len(pix)))
        _PROTO_OUT.write(pix)
        _PROTO_OUT.flush()

    def run_ipc(self):
        parent_pid = os.getppid()
        if os.name == "posix" and parent_pid > 1:
            def _watch_parent_posix():
                while True:
                    time.sleep(0.75)
                    if os.getppid() != parent_pid or os.getppid() <= 1:
                        os._exit(0)
            threading.Thread(target=_watch_parent_posix, daemon=True).start()
        elif os.name == "nt" and parent_pid > 0:
            def _watch_parent_win():
                import ctypes
                k32 = ctypes.windll.kernel32
                h = k32.OpenProcess(0x00100000, False, parent_pid)  # SYNCHRONIZE
                if h:
                    k32.WaitForSingleObject(h, 0xFFFFFFFF)
                    k32.CloseHandle(h)
                    os._exit(0)
            threading.Thread(target=_watch_parent_win, daemon=True).start()

        last_pix = bytes([KEY]) * (W * H)
        for raw_bytes in sys.stdin.buffer:
            line = raw_bytes.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                if req.get("cmd") == "quit":
                    break
                meta, pix = self.render_step(req)
                last_pix = pix
            except Exception as exc:
                m = getattr(self, "rig", None)
                title = m.m.title if m is not None else "VRM"
                meta = {
                    "w": W,
                    "h": H,
                    "state": "idle",
                    "facing": 1,
                    "vx": 0.0,
                    "sfx": [],
                    "title": title,
                    "version": "",
                    "author": "",
                    "tris": 0,
                    "exprCount": 0,
                    "style": self.style,
                    "outline": self.outline_on,
                    "morphFx": False,
                    "introDone": False,
                    "status": f"Engine err: {exc}",
                    "statusError": True,
                    "palVer": self.pal_ver,
                }
                pix = last_pix
            self._emit_packet(meta, pix)


def run_verify_sheet(out_png, vrm_path):
    from PIL import Image, ImageDraw
    eng = WalkRenderer(vrm_path)
    cases = [
        ("IDLE", dict(auto_mode=False, force_state="idle", facing=1)),
        ("WALK", dict(auto_mode=False, force_state="walk", facing=-1, gait=1.15)),
        ("RUN", dict(auto_mode=False, force_state="run", facing=1, gait=2.35)),
        ("WAVE", dict(auto_mode=False, force_state="wave", facing=1)),
        ("FLOOR_SIT", dict(auto_mode=False, force_state="sit", facing=1, on_edge=False)),
        ("EDGE_SIT", dict(auto_mode=False, force_state="sit", facing=1, on_edge=True)),
        ("NAP", dict(auto_mode=False, force_state="nap", facing=-1, on_edge=True)),
        ("PET", dict(pet=True)),
        ("GRAB", dict(grabbing=True, drag_vx=-340.0)),
        ("INTRO_BIG", dict(intro_demo=True)),
    ]

    cols_rgb = np.array([((c >> 16) & 255, (c >> 8) & 255, c & 255) for c in eng.active_cols], dtype=np.uint8)
    yy, xx = np.mgrid[0:H, 0:W]
    chk = (((xx // 8) + (yy // 8)) & 1) == 0
    bg = np.where(chk[..., None], np.array([228, 232, 240], np.uint8), np.array([204, 210, 222], np.uint8))

    sheet = Image.new("RGB", (W * 5, H * 2))
    draw = ImageDraw.Draw(sheet)
    for k, (label, cfg) in enumerate(cases):
        eng.brain = PR.MascotBrain()
        eng.particles = []
        eng.morph_fx_t = 0.0
        eng.intro_t = -1.0
        state = cfg.get("force_state", "idle")
        eng.brain.trigger_state(state, 5.0)
        eng.brain.facing = cfg.get("facing", 1)
        eng.brain.gait_phase = cfg.get("gait", 0.0)
        if cfg.get("pet"):
            eng._spawn_particle("heart", W * 0.5 - 12, 30)
            eng._spawn_particle("heart", W * 0.5 + 14, 22)
        if state == "nap":
            eng._spawn_particle("z", W * 0.5 + 18, 50)
            eng._spawn_particle("z", W * 0.5 + 24, 38)
        for step_i in range(20):
            if cfg.get("intro_demo") and step_i == 19:
                eng.intro_t = 0.20
                for sx, sy in ((34, 48), (108, 44), (40, 114), (106, 108), (72, 26)):
                    eng._spawn_particle("sparkle", sx, sy)
            _, pix = eng.render_step({
                "dt": 1.0 / 30.0,
                "auto_mode": cfg.get("auto_mode", True),
                "force_state": cfg.get("force_state", ""),
                "pet": bool(cfg.get("pet", False)),
                "grabbing": bool(cfg.get("grabbing", False)),
                "drag_vx": float(cfg.get("drag_vx", 0.0)),
                "on_window_edge": bool(cfg.get("on_edge", False)),
                "intro_big": bool(cfg.get("intro_demo") and step_i == 19),
                "mx": 0.4 if label == "IDLE" else 0.0,
                "my": 0.2 if label == "IDLE" else 0.0,
                "hover": label == "IDLE",
            })
        idx = np.frombuffer(pix, dtype=np.uint8).reshape(H, W)
        rgb = np.where(idx[..., None] == KEY, bg, cols_rgb[idx])
        tile = Image.fromarray(rgb)
        gx, gy = (k % 5) * W, (k // 5) * H
        sheet.paste(tile, (gx, gy))
        if label == "EDGE_SIT" or label == "NAP":
            # Subtle dashed indicator showing where a host window's titlebar top edge sits under her thighs
            draw.line([gx + 34, gy + H - 46, gx + W - 34, gy + H - 46], fill=(255, 226, 122), width=2)
        draw.rectangle([gx + 2, gy + 2, gx + 66, gy + 15], fill=(22, 18, 36))
        draw.text((gx + 5, gy + 4), label, fill=(255, 226, 122))

    os.makedirs(os.path.dirname(out_png), exist_ok=True)
    sheet.save(out_png)
    print(f"Saved {out_png} ({sheet.width}x{sheet.height})")


if __name__ == "__main__":
    if "--verify-sheet" in sys.argv:
        i = sys.argv.index("--verify-sheet")
        out_path = os.path.abspath(sys.argv[i + 1])
        rest = [a for j, a in enumerate(sys.argv[1:], 1) if j not in (i, i + 1)]
        vrm = os.path.abspath(rest[0]) if rest else None
        run_verify_sheet(out_path, vrm)
    else:
        args = sys.argv[1:]
        sample = None
        if "--sample" in args:
            k = args.index("--sample")
            if k + 1 < len(args):
                sample = os.path.abspath(args[k + 1])
            args = args[:k] + args[k + 2:]
        rest = [a for a in args if a != "--ipc"]
        vrm = os.path.abspath(rest[0]) if rest else None
        WalkRenderer(vrm, sample).run_ipc()
