"""procedural_rig - Calculated Shimeji-style procedural animations (v3):
- Dual sit mode: grounded floor-sit on desktop bottom OR window-edge perch sit with dangling legs
- Limp dangling grab pose with pendulum velocity sway
- Scale-aware walking speed + cute surprise reaction when corner-resized
"""
# SPDX-License-Identifier: MIT
import math
import random

import numpy as np

import vrm_io as V

AUTO_STATES = ("idle", "walk", "run", "tilt", "wave", "stretch", "sit", "nap")


def _quat_of(M):
    R = M[:3, :3] / np.maximum(np.linalg.norm(M[:3, :3], axis=0), 1e-8)
    return V.mat_to_quat(R)


def _qrot(q, v):
    u = q[:3]
    return v + 2.0 * np.cross(u, np.cross(u, v) + q[3] * v)


def q_euler_xyz(rx_deg=0.0, ry_deg=0.0, rz_deg=0.0):
    """Build unit quaternion (x, y, z, w) applying world Z, then X, then Y (R_y * R_x * R_z)."""
    hx = math.radians(rx_deg) * 0.5
    hy = math.radians(ry_deg) * 0.5
    hz = math.radians(rz_deg) * 0.5
    cx, sx = math.cos(hx), math.sin(hx)
    cy, sy = math.cos(hy), math.sin(hy)
    cz, sz = math.cos(hz), math.sin(hz)
    qxz_x = sx * cz
    qxz_y = -sx * sz
    qxz_z = cx * sz
    qxz_w = cx * cz
    return np.array([
        cy * qxz_x + sy * qxz_z,
        sy * qxz_w + cy * qxz_y,
        cy * qxz_z - sy * qxz_x,
        cy * qxz_w - sy * qxz_y,
    ], dtype=np.float32)


def q_nlerp_batch(a, b, alpha):
    """Hemisphere-aligned normalized lerp across (N, 4) quaternions."""
    dot = np.sum(a * b, axis=1, keepdims=True)
    b_aligned = np.where(dot < 0.0, -b, b)
    q = a + (b_aligned - a) * alpha
    norm = np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-8)
    return (q / norm).astype(np.float32)


def auto_blink_weights(t, available_exprs, force_closed=False):
    w_vec = np.zeros(len(V.EXPRESSION_NAMES), dtype=np.float32)
    if not available_exprs:
        return w_vec
    val = 0.0
    if force_closed:
        val = 1.0
    else:
        ph = float(t) % 3.6
        if ph < 0.16:
            val = math.sin(math.pi * ph / 0.16)
    if val > 1e-4:
        if 0 in available_exprs:
            w_vec[0] = val
        else:
            if 1 in available_exprs:
                w_vec[1] = val
            if 2 in available_exprs:
                w_vec[2] = val
    return w_vec


def _box_mesh(hx, hy, hz):
    faces = [
        ((-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)),
        ((hx, -hy, -hz), (-hx, -hy, -hz), (-hx, hy, -hz), (hx, hy, -hz)),
        ((-hx, hy, hz), (hx, hy, hz), (hx, hy, -hz), (-hx, hy, -hz)),
        ((-hx, -hy, -hz), (hx, -hy, -hz), (hx, -hy, hz), (-hx, -hy, hz)),
        ((hx, -hy, hz), (hx, -hy, -hz), (hx, hy, -hz), (hx, hy, hz)),
        ((-hx, -hy, -hz), (-hx, -hy, hz), (-hx, hy, hz), (-hx, hy, -hz)),
    ]
    pos, uv, idx = [], [], []
    for k, (a, b, c, d) in enumerate(faces):
        base = k * 4
        pos.extend([a, b, c, d])
        uv.extend([(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)])
        idx.extend([base, base + 1, base + 2, base, base + 2, base + 3])
    return np.array(pos, np.float32), np.array(uv, np.float32), np.array(idx, np.int64)


class ProceduralModel:
    """Built-in CC0 Retro Bot fallback if no VRM file is available."""

    def __init__(self):
        self.version = "Builtin"
        self.title = "Retro Bot"
        self.author = "haruka_apps"
        self.license = ["BUILTIN CC0 MASCOT"]
        self.warnings = []
        self.face_yaw = 0.0
        self.height = 1.60
        self.expression_names = list(V.EXPRESSION_NAMES)
        self.available_expressions = []
        self.has_expressions = False
        self.human = {
            "hips": 0, "spine": 1, "head": 2,
            "leftUpperArm": 3, "rightUpperArm": 4,
            "leftUpperLeg": 5, "rightUpperLeg": 6,
        }
        self.names = ["hips", "torso", "head", "l_arm", "r_arm", "l_leg", "r_leg"]
        self.parent = np.array([-1, 0, 1, 1, 1, 0, 0], np.int64)
        self.order = np.arange(7, dtype=np.int64)
        self.rest_t = np.array([
            [0.0, 0.85, 0.0], [0.0, 0.25, 0.0], [0.0, 0.34, 0.0],
            [0.24, 0.16, 0.0], [-0.24, 0.16, 0.0],
            [0.10, -0.35, 0.0], [-0.10, -0.35, 0.0],
        ], np.float32)
        self.rest_q = np.tile(np.array([0, 0, 0, 1], np.float32), (7, 1))
        self.rest_s = np.ones((7, 3), np.float32)
        self.rest_m = self.local_mats(self.rest_q, self.rest_t)
        self.rest_world = self.world(self.rest_m)

        pal = np.array([
            [118, 150, 222], [112, 198, 169], [233, 195, 91],
            [212, 24, 108], [238, 238, 238], [57, 92, 152],
        ], np.uint8)
        self.palette = np.vstack([pal, np.tile(pal[-1:], (239 - len(pal), 1))])
        self.rgba, self.tex_index = [], []
        for k, col in enumerate(pal):
            a = np.zeros((4, 4, 4), np.uint8)
            a[..., :3] = col
            a[..., 3] = 255
            self.rgba.append(a)
            self.tex_index.append(np.full((4, 4), 16 + k, np.uint8))

        specs = [
            (0, (0.14, 0.09, 0.10), 5), (1, (0.16, 0.22, 0.11), 0),
            (2, (0.14, 0.14, 0.14), 4), (3, (0.06, 0.24, 0.06), 1),
            (4, (0.06, 0.24, 0.06), 1), (5, (0.07, 0.28, 0.07), 2),
            (6, (0.07, 0.28, 0.07), 3),
        ]
        self.parts = []
        self.tris = 0
        for ni, (sx, sy, sz), tex in specs:
            pos, uv, idx = _box_mesh(sx, sy, sz)
            n = len(pos)
            ww = np.zeros((n, 4), np.float32)
            ww[:, 0] = 1.0
            slot = np.zeros((n, 4), np.int64)
            p1 = np.hstack([pos, np.ones((n, 1), np.float32)])
            wpos = (self.rest_world[ni].astype(np.float32) @ p1.T)[:3].T.astype(np.float32)
            self.parts.append(dict(
                pos=wpos, uv=uv, idx=idx, slot=slot, ww=ww,
                skin_nodes=np.array([ni], np.int64),
                ibm=np.linalg.inv(self.rest_world[ni]).astype(np.float32)[None],
                tex=tex, name=self.names[ni], alpha="OPAQUE", double=False, keyed=False,
                morphs={},
            ))
            self.tris += len(idx) // 3
        self.verts = sum(len(p["pos"]) for p in self.parts)

    def world(self, local):
        W = np.empty_like(local)
        for i in self.order:
            p = self.parent[i]
            W[i] = local[i] if p < 0 else W[p] @ local[i]
        return W

    def local_mats(self, q, t=None):
        t = self.rest_t if t is None else t
        x, y, z, w = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
        M = np.zeros((len(q), 4, 4), np.float64)
        M[:, 0, 0] = 1 - 2 * (y * y + z * z)
        M[:, 0, 1] = 2 * (x * y - z * w)
        M[:, 0, 2] = 2 * (x * z + y * w)
        M[:, 1, 0] = 2 * (x * y + z * w)
        M[:, 1, 1] = 1 - 2 * (x * x + z * z)
        M[:, 1, 2] = 2 * (y * z - x * w)
        M[:, 2, 0] = 2 * (x * z - y * w)
        M[:, 2, 1] = 2 * (y * z + x * w)
        M[:, 2, 2] = 1 - 2 * (x * x + y * y)
        M[:, :3, 3] = t
        M[:, 3, 3] = 1
        return M


class MascotBrain:
    """Autonomous Shimeji-style procedural mascot brain supporting floor + window-edge perching."""

    BONES = [
        "hips", "spine", "chest", "upperChest", "neck", "head",
        "leftShoulder", "leftUpperArm", "leftLowerArm", "leftHand",
        "rightShoulder", "rightUpperArm", "rightLowerArm", "rightHand",
        "leftUpperLeg", "leftLowerLeg", "leftFoot",
        "rightUpperLeg", "rightLowerLeg", "rightFoot",
    ]
    IDX = {b: k for k, b in enumerate(BONES)}

    def __init__(self, seed=20261126):
        self.rng = random.Random(seed)
        self.floor_sit_scale = 1.0
        self.state = "wave"
        self.state_t = 0.0
        self.state_dur = 2.4
        self.facing = -1
        self.gait_phase = 0.0
        self.pet_timer = 0.0
        self.land_timer = 0.0
        self.run_boost_timer = 0.0
        self.tilt_sign = 1.0
        self.sfx_queue = []
        self._last_step_half = -1

        n = len(self.BONES)
        self.cur_D = np.tile(np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32), (n, 1))
        self.cur_hp = np.zeros(3, dtype=np.float32)
        self.cur_yaw = 0.0
        self.cur_expr = np.zeros(len(V.EXPRESSION_NAMES), dtype=np.float32)
        self.look_x = 0.0
        self.look_y = 0.0

    def trigger_state(self, state, duration=None):
        self.state = state
        self.state_t = 0.0
        self._last_step_half = -1
        if duration is not None:
            self.state_dur = duration
        else:
            self.state_dur = {
                "idle": self.rng.uniform(2.6, 5.2),
                "walk": self.rng.uniform(3.2, 6.2),
                "run": self.rng.uniform(1.8, 3.2),
                "tilt": self.rng.uniform(1.8, 2.8),
                "wave": self.rng.uniform(2.0, 2.8),
                "stretch": self.rng.uniform(2.2, 3.0),
                "sit": self.rng.uniform(4.5, 8.5),
                "nap": self.rng.uniform(5.5, 9.5),
            }.get(state, 3.5)

    def _pick_next_auto_state(self, on_window_edge):
        cur = self.state
        if on_window_edge:
            # Prefer cozy edge-sitting & patrolling along the window edge
            if cur == "walk":
                choices = [("sit", 4), ("idle", 2), ("tilt", 2), ("nap", 2), ("wave", 1)]
            elif cur == "sit":
                choices = [("nap", 3), ("walk", 3), ("idle", 2), ("tilt", 2)]
            else:
                choices = [("sit", 4), ("walk", 4), ("idle", 2), ("tilt", 2), ("stretch", 1)]
        else:
            if cur == "walk":
                choices = [("idle", 3), ("sit", 2), ("tilt", 2), ("run", 2), ("wave", 1), ("stretch", 1)]
            elif cur == "run":
                choices = [("idle", 3), ("walk", 3), ("wave", 1)]
            elif cur == "sit":
                choices = [("nap", 3), ("idle", 3), ("walk", 2), ("tilt", 1)]
            elif cur == "nap":
                choices = [("stretch", 4), ("sit", 2), ("idle", 2)]
            else:
                choices = [("walk", 5), ("tilt", 2), ("wave", 2), ("sit", 2), ("stretch", 1), ("run", 2)]
        pool = [s for s, w in choices for _ in range(w)]
        nxt = self.rng.choice(pool)
        if nxt in ("walk", "run") and self.rng.random() < 0.35:
            self.facing = -self.facing
        if nxt == "tilt":
            self.tilt_sign = -self.tilt_sign
        self.trigger_state(nxt)

    def _compute_target_pose(
        self, state, t, gait, facing, drag_vx, pet_t, look_x, look_y,
        available_exprs, on_window_edge, resizing, scale_factor,
    ):
        n = len(self.BONES)
        D = np.tile(np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32), (n, 1))
        hp = np.zeros(3, dtype=np.float32)
        expr = auto_blink_weights(t, available_exprs, force_closed=(state == "nap"))
        vx = 0.0
        body_yaw = 0.0

        l_shoulder = (0.0, 0.0, 0.0)
        r_shoulder = (0.0, 0.0, 0.0)
        l_arm = (-5.0, 0.0, -80.0)
        r_arm = (-5.0, 0.0, 80.0)
        l_elbow = (-18.0, 0.0, -79.0)
        r_elbow = (-18.0, 0.0, 79.0)
        l_thigh_rx, l_knee = 0.0, 4.0
        r_thigh_rx, r_knee = 0.0, 4.0
        l_thigh_rz, r_thigh_rz = 1.0, -1.0
        l_foot_rx, r_foot_rx = 0.0, 0.0
        spine_rot = (1.5, 0.0, 0.0)
        chest_rot = (1.0, 0.0, 0.0)
        neck_rot = (0.0, 0.0, 0.0)
        head_rot = (0.0, 0.0, 0.0)
        hips_rot = (0.0, 0.0, 0.0)

        look_scale = 1.0 if state in ("idle", "tilt", "sit", "wave", "pet") else 0.35
        lx = max(-1.0, min(1.0, look_x)) * look_scale
        ly = max(-1.0, min(1.0, look_y)) * look_scale

        # Size-linked walk personality: tiny sprite steps faster, big walks gently
        sf = max(0.55, min(2.2, float(scale_factor)))
        speed_mult = 0.85 + 0.25 / sf

        if state == "idle":
            breath = math.sin(t * 2.1)
            sway = math.sin(t * 1.1)
            hp[1] = 0.008 * breath
            spine_rot = (1.5 + 1.4 * breath, 0.0, 1.8 * sway)
            chest_rot = (1.0 + 1.6 * breath, 0.0, -1.2 * sway)
            l_arm = (-4.0, 0.0, -80.0 - 1.5 * breath)
            r_arm = (-4.0, 0.0, 80.0 + 1.5 * breath)
            l_elbow = (-18.0 - 3.0 * breath, 0.0, -79.0)
            r_elbow = (-18.0 - 3.0 * breath, 0.0, 79.0)
            head_rot = (2.0 * breath, 0.0, 2.5 * sway)

        elif state in ("walk", "run"):
            is_run = state == "run"
            amp_leg = 34.0 if is_run else 24.0
            amp_arm = 42.0 if is_run else 24.0
            lean = 12.0 if is_run else 4.5
            bounce = 0.055 if is_run else 0.028
            vx = facing * (140.0 if is_run else 62.0) * speed_mult
            body_yaw = facing * (70.0 if is_run else 64.0)

            s = math.sin(gait)
            c = math.cos(gait)
            abs_s = abs(s)

            hp[1] = -0.02 + bounce * (1.0 - abs_s)
            hips_rot = (lean * 0.25, 6.0 * s, 2.5 * c)
            spine_rot = (lean * 0.55, -5.0 * s, 0.0)
            chest_rot = (lean * 0.45, -7.0 * s, 0.0)
            head_rot = (-lean * 0.45 + 2.5 * abs_s, 0.0, 2.5 * s)

            l_thigh_rx = -amp_leg * s
            r_thigh_rx = amp_leg * s
            l_knee = max(2.0, (28.0 if is_run else 18.0) * max(0.0, -c) + (18.0 if is_run else 8.0) * max(0.0, -s))
            r_knee = max(2.0, (28.0 if is_run else 18.0) * max(0.0, c) + (18.0 if is_run else 8.0) * max(0.0, s))

            arm_drop = -69.0 if is_run else -73.0
            r_arm = (-amp_arm * s, 0.0, -arm_drop)
            l_arm = (amp_arm * s, 0.0, arm_drop)
            elbow_flex = 48.0 if is_run else 24.0
            l_elbow = (l_arm[0] - elbow_flex - max(0.0, -s) * 16.0, 0.0, arm_drop)
            r_elbow = (r_arm[0] - elbow_flex - max(0.0, s) * 16.0, 0.0, -arm_drop)

            if is_run and 8 in available_exprs:
                expr[8] = 0.55

        elif state == "tilt":
            w = math.sin(min(1.0, t / 0.35) * math.pi * 0.5)
            head_rot = (4.0, 6.0 * self.tilt_sign, 21.0 * self.tilt_sign * w)
            neck_rot = (2.0, 0.0, 6.0 * self.tilt_sign * w)
            chest_rot = (2.0, 0.0, 4.0 * self.tilt_sign * w)
            l_elbow = (-26.0, 0.0, -78.0)
            r_elbow = (-26.0, 0.0, 78.0)
            if 12 in available_exprs and t < 0.8:
                expr[12] = 0.45 * (1.0 - t / 0.8)

        elif state == "wave":
            wave_osc = math.sin(t * 9.5) * 16.0
            body_yaw = 10.0
            chest_rot = (-2.0, 4.0, -4.0)
            head_rot = (-2.0, 0.0, -7.0)
            r_arm = (14.0, 0.0, -42.0)
            r_elbow = (18.0, 0.0, -96.0 + wave_osc)
            l_elbow = (-24.0, 0.0, -78.0)
            if 8 in available_exprs:
                expr[8] = 0.85

        elif state == "stretch":
            env = math.sin(min(1.0, t / max(0.2, self.state_dur)) * math.pi)
            hp[1] = 0.03 * env
            spine_rot = (-7.0 * env, 0.0, 0.0)
            chest_rot = (-9.0 * env, 0.0, 0.0)
            head_rot = (-8.0 * env, 0.0, 6.0 * math.sin(t * 2.5) * env)
            raise_deg = 78.0 - 142.0 * env
            r_arm = (18.0 * env, 0.0, raise_deg)
            l_arm = (18.0 * env, 0.0, -raise_deg)
            r_elbow = (22.0 * env, 0.0, raise_deg - 15.0 * env)
            l_elbow = (22.0 * env, 0.0, -raise_deg + 15.0 * env)
            if 11 in available_exprs:
                expr[11] = 0.8 * env
            if 7 in available_exprs and 0.25 < env:
                expr[7] = 0.45 * env

        elif state in ("sit", "nap"):
            is_nap = state == "nap"
            breath = math.sin(t * (1.35 if is_nap else 2.2))
            if on_window_edge:
                # Window-edge perch sit: modest 3/4 view, knees together, legs dangling over titlebar!
                body_yaw = facing * 48.0
                hp[1] = -0.34 + 0.004 * breath
                hp[2] = -0.04
                hips_rot = (-4.0, 0.0, 0.0)
                l_thigh_rx = -76.0
                r_thigh_rx = -76.0
                l_thigh_rz = -2.0
                r_thigh_rz = 2.0
                swing_l = 0.0 if is_nap else 11.0 * math.sin(t * 2.5)
                swing_r = 0.0 if is_nap else -11.0 * math.sin(t * 2.5)
                l_knee = 85.0 + swing_l
                r_knee = 85.0 + swing_r
                l_arm = (-22.0, 0.0, -76.0)
                r_arm = (-22.0, 0.0, 76.0)
                l_elbow = (-48.0, 0.0, -72.0)
                r_elbow = (-48.0, 0.0, 72.0)
            else:
                # Grounded desktop floor-sit calibrated to VRM hip height vs lowest vertex
                body_yaw = facing * 56.0
                hp[1] = -0.84 * self.floor_sit_scale + 0.004 * breath
                hp[2] = -0.14
                hips_rot = (-8.0, 0.0, 0.0)
                l_thigh_rx = -84.0
                r_thigh_rx = -84.0
                l_thigh_rz = -2.0
                r_thigh_rz = 2.0
                l_knee = 18.0 + (0.0 if is_nap else 3.0 * math.sin(t * 1.9))
                r_knee = 18.0 + (0.0 if is_nap else -3.0 * math.sin(t * 1.9))
                l_foot_rx = -28.0
                r_foot_rx = -28.0
                l_arm = (-18.0, 0.0, -76.0)
                r_arm = (-18.0, 0.0, 76.0)
                l_elbow = (-46.0, 0.0, -72.0)
                r_elbow = (-46.0, 0.0, 72.0)

            if is_nap:
                nod = 18.0 + 4.5 * math.sin(t * 1.1)
                spine_rot = (10.0 + 2.0 * breath, 0.0, 2.5)
                chest_rot = (8.0 + 1.5 * breath, 0.0, 0.0)
                neck_rot = (nod * 0.45, -10.0 * facing, 4.0)
                head_rot = (nod * 0.65, -12.0 * facing, 7.0)
                if 11 in available_exprs:
                    expr[11] = 0.45
            else:
                spine_rot = (5.0, 0.0, 0.0)
                neck_rot = (2.0, -14.0 * facing, 0.0)
                head_rot = (3.0 * breath, -18.0 * facing, 3.5 * math.sin(t * 0.9))

        elif state == "pet":
            wiggle = math.sin(t * 7.5)
            hp[1] = 0.015 * abs(wiggle)
            chest_rot = (2.0, 3.0 * wiggle, 4.0 * wiggle)
            head_rot = (-4.0, 4.0 * wiggle, 10.0 * wiggle)
            l_arm = (-10.0, 0.0, -78.0)
            r_arm = (-10.0, 0.0, 78.0)
            l_elbow = (-38.0, 0.0, -74.0)
            r_elbow = (-38.0, 0.0, 74.0)
            if 8 in available_exprs:
                expr[8] = 0.95
            if 11 in available_exprs:
                expr[11] = 0.35

        elif state == "grab":
            pend = max(-20.0, min(20.0, -drag_vx * 0.042))
            arm_lag = max(-14.0, min(14.0, -drag_vx * 0.028))
            kick = math.sin(t * 11.5)
            hp[1] = 0.04
            hips_rot = (4.0, 0.0, pend)
            spine_rot = (3.0, 0.0, pend * 0.30)
            chest_rot = (2.0, 0.0, pend * 0.20)
            head_rot = (8.0, 0.0, -pend * 0.35 + 3.5 * kick)
            l_shoulder = (0.0, 0.0, 10.0)
            r_shoulder = (0.0, 0.0, -10.0)
            l_arm = (-8.0, 0.0, -79.0 + arm_lag + 3.0 * kick)
            r_arm = (-8.0, 0.0, 79.0 + arm_lag - 3.0 * kick)
            l_elbow = (-20.0, 0.0, -82.0 + arm_lag * 1.15)
            r_elbow = (-20.0, 0.0, 82.0 + arm_lag * 1.15)
            l_thigh_rx = -8.0 + 13.0 * kick
            r_thigh_rx = -8.0 - 13.0 * kick
            l_knee = 14.0 + 15.0 * max(0.0, -kick)
            r_knee = 14.0 + 15.0 * max(0.0, kick)
            if 12 in available_exprs:
                expr[12] = 0.85

        elif state == "fall":
            flail = math.sin(t * 15.0)
            hp[1] = 0.03
            hips_rot = (6.0, 0.0, 0.0)
            l_arm = (-10.0, 0.0, -68.0 + 10.0 * flail)
            r_arm = (-10.0, 0.0, 68.0 - 10.0 * flail)
            l_elbow = (-28.0, 0.0, -72.0)
            r_elbow = (-28.0, 0.0, 72.0)
            l_thigh_rx = -14.0 + 9.0 * flail
            r_thigh_rx = -14.0 - 9.0 * flail
            l_knee = 24.0
            r_knee = 24.0
            if 12 in available_exprs:
                expr[12] = 0.75

        elif state == "land":
            crouch = math.sin(min(1.0, t / 0.32) * math.pi)
            hp[1] = -0.15 * crouch
            hips_rot = (8.0 * crouch, 0.0, 0.0)
            spine_rot = (10.0 * crouch, 0.0, 0.0)
            l_thigh_rx = -34.0 * crouch
            r_thigh_rx = -34.0 * crouch
            l_knee = 66.0 * crouch
            r_knee = 66.0 * crouch
            l_arm = (-6.0, 0.0, -74.0)
            r_arm = (-6.0, 0.0, 74.0)
            l_elbow = (-26.0, 0.0, -70.0)
            r_elbow = (-26.0, 0.0, 70.0)

        if resizing:
            if 12 in available_exprs:
                expr[12] = max(expr[12], 0.65)
            head_rot = (head_rot[0] - 4.0, head_rot[1], head_rot[2] + 6.0 * math.sin(t * 8.0))
            hp[1] += 0.015 * math.sin(t * 14.0)

        yaw_look = 22.0 * lx
        pitch_look = -14.0 * ly
        neck_rot = (neck_rot[0] + pitch_look * 0.35, neck_rot[1] + yaw_look * 0.35, neck_rot[2])
        head_rot = (head_rot[0] + pitch_look * 0.65, head_rot[1] + yaw_look * 0.65, head_rot[2])

        I = self.IDX
        D[I["hips"]] = q_euler_xyz(*hips_rot)
        D[I["spine"]] = q_euler_xyz(*spine_rot)
        D[I["chest"]] = q_euler_xyz(*chest_rot)
        D[I["upperChest"]] = q_euler_xyz(chest_rot[0] * 0.5, chest_rot[1] * 0.5, 0.0)
        D[I["neck"]] = q_euler_xyz(*neck_rot)
        D[I["head"]] = q_euler_xyz(*head_rot)

        D[I["leftShoulder"]] = q_euler_xyz(*l_shoulder)
        D[I["leftUpperArm"]] = q_euler_xyz(*l_arm)
        D[I["leftLowerArm"]] = q_euler_xyz(*l_elbow)
        D[I["leftHand"]] = D[I["leftLowerArm"]]
        D[I["rightShoulder"]] = q_euler_xyz(*r_shoulder)
        D[I["rightUpperArm"]] = q_euler_xyz(*r_arm)
        D[I["rightLowerArm"]] = q_euler_xyz(*r_elbow)
        D[I["rightHand"]] = D[I["rightLowerArm"]]

        D[I["leftUpperLeg"]] = q_euler_xyz(l_thigh_rx, 0.0, l_thigh_rz)
        D[I["leftLowerLeg"]] = q_euler_xyz(l_thigh_rx + max(0.0, l_knee), 0.0, l_thigh_rz)
        D[I["leftFoot"]] = q_euler_xyz(
            l_foot_rx if abs(l_foot_rx) > 1e-3 else max(-12.0, l_thigh_rx + l_knee) * 0.15, 0.0, 0.0
        )

        D[I["rightUpperLeg"]] = q_euler_xyz(r_thigh_rx, 0.0, r_thigh_rz)
        D[I["rightLowerLeg"]] = q_euler_xyz(r_thigh_rx + max(0.0, r_knee), 0.0, r_thigh_rz)
        D[I["rightFoot"]] = q_euler_xyz(
            r_foot_rx if abs(r_foot_rx) > 1e-3 else max(-12.0, r_thigh_rx + r_knee) * 0.15, 0.0, 0.0
        )

        return D, hp, body_yaw, np.clip(expr, 0.0, 1.0), vx

    def step(
        self, dt, available_exprs, *,
        mx=0.0, my=0.0, hover=False, pet=False, poke=False, dblclick=False,
        grabbing=False, drag_vx=0.0, airborne=False, just_landed=False,
        just_perched=False, on_window_edge=False, resizing=False,
        scale_factor=1.0, hit_edge=0, auto_mode=True, force_state="",
    ):
        dt = max(1e-3, min(0.08, float(dt)))
        self.state_t += dt

        tx = mx if hover else 0.0
        ty = my if hover else 0.0
        la = 1.0 - math.exp(-dt * 8.0)
        self.look_x += (tx - self.look_x) * la
        self.look_y += (ty - self.look_y) * la

        if hit_edge < 0 and self.facing < 0:
            self.facing = 1
        elif hit_edge > 0 and self.facing > 0:
            self.facing = -1

        if pet:
            if self.pet_timer <= 0.0:
                self.sfx_queue.append("pet")
            self.pet_timer = 1.15
        else:
            self.pet_timer = max(0.0, self.pet_timer - dt)

        if dblclick:
            self.facing = -self.facing if self.rng.random() < 0.4 else self.facing
            self.run_boost_timer = 2.4
            self.sfx_queue.append("wave")
        elif self.run_boost_timer > 0.0:
            self.run_boost_timer = max(0.0, self.run_boost_timer - dt)

        if poke and not grabbing and not airborne:
            if self.state in ("sit", "nap"):
                self.trigger_state("idle", 2.4)
            else:
                self.trigger_state("wave", 2.0)
            self.sfx_queue.append("wave")

        if just_perched:
            # Dropping onto an app window edge immediately seats mascot on the edge!
            self.trigger_state("sit", 5.0)
            self.sfx_queue.append("land")
            self.land_timer = 0.0
        elif just_landed:
            self.land_timer = 0.34
            self.sfx_queue.append("land")
        else:
            self.land_timer = max(0.0, self.land_timer - dt)

        if grabbing:
            eff_state = "grab"
            if self.state != "grab":
                self.state = "grab"
                self.state_t = 0.0
                self.sfx_queue.append("grab")
        elif airborne:
            eff_state = "fall"
            if self.state != "fall":
                self.state = "fall"
                self.state_t = 0.0
        elif self.land_timer > 0.0:
            eff_state = "land"
        elif self.pet_timer > 0.0:
            eff_state = "pet"
        elif self.run_boost_timer > 0.0:
            eff_state = "run"
        elif not auto_mode and force_state in AUTO_STATES:
            if self.state != force_state:
                self.trigger_state(force_state, 999.0)
            eff_state = force_state
        else:
            if self.state in ("grab", "fall"):
                self.trigger_state("sit" if on_window_edge else "idle", 3.6)
            elif self.state_t >= self.state_dur:
                self._pick_next_auto_state(on_window_edge)
            eff_state = self.state

        if eff_state == "walk":
            self.gait_phase += dt * 7.6 * (0.9 + 0.15 / max(0.6, scale_factor))
            half = int(self.gait_phase / math.pi)
            if half != self._last_step_half:
                self._last_step_half = half
                self.sfx_queue.append("step")
        elif eff_state == "run":
            self.gait_phase += dt * 11.4 * (0.9 + 0.15 / max(0.6, scale_factor))
            half = int(self.gait_phase / math.pi)
            if half != self._last_step_half:
                self._last_step_half = half
                self.sfx_queue.append("step")

        tgt_D, tgt_hp, tgt_yaw, tgt_expr, vx = self._compute_target_pose(
            eff_state, self.state_t, self.gait_phase, self.facing,
            drag_vx, self.pet_timer, self.look_x, self.look_y, available_exprs,
            on_window_edge, resizing, scale_factor,
        )

        rate = 20.0 if eff_state in ("grab", "fall", "land") else 11.0
        alpha = 1.0 - math.exp(-dt * rate)
        self.cur_D = q_nlerp_batch(self.cur_D, tgt_D, alpha)
        self.cur_hp = (self.cur_hp + (tgt_hp - self.cur_hp) * alpha).astype(np.float32)
        self.cur_yaw = self.cur_yaw + (tgt_yaw - self.cur_yaw) * min(1.0, alpha * 0.9)
        self.cur_expr = (self.cur_expr + (tgt_expr - self.cur_expr) * min(1.0, alpha * 1.4)).astype(np.float32)

        return eff_state, vx


class RigState:
    """Evaluates humanoid world rotations, overall facing yaw, and 13-expression morph skinning."""

    def __init__(self, model):
        self.m = model
        self.p1 = [np.hstack([p["pos"], np.ones((len(p["pos"]), 1), np.float32)]) for p in model.parts]
        self.rest_wq = np.array([_quat_of(M) for M in model.rest_world])
        self.bone_nodes = [model.human.get(b, -1) for b in MascotBrain.BONES]
        self.node_bone = {n: k for k, n in enumerate(self.bone_nodes) if n >= 0}
        hips = model.human.get("hips")
        self.hips = hips
        self.hip_h = float(model.rest_world[hips][1, 3]) if hips is not None else 1.0
        self.flip = getattr(model, "version", "") == "VRM 0.x"
        self.has_humanoid = hips is not None
        self.yaw_sign = -1.0 if model.face_yaw == 180.0 else 1.0

    def pose_from_brain(self, brain_D, brain_hp, body_yaw_deg=0.0):
        m = self.m
        q = m.rest_q.astype(np.float64).copy()
        tr = m.rest_t.copy()
        if not self.has_humanoid:
            return q, tr

        D = brain_D.copy()
        hp = brain_hp.copy()

        if abs(body_yaw_deg) > 1e-3:
            qy = q_euler_xyz(0.0, body_yaw_deg, 0.0)
            D = np.array([V.qmul(qy, d) for d in D], dtype=np.float32)
            hp = _qrot(qy, hp).astype(np.float32)

        if self.flip:
            D = D * np.array([-1.0, 1.0, -1.0, 1.0], np.float32)
            hp = hp * np.array([-1.0, 1.0, -1.0], np.float32)

        Wq = {}
        one = np.array([0.0, 0.0, 0.0, 1.0])
        for i in m.order:
            p = m.parent[i]
            pw = Wq.get(p) if p >= 0 else None
            if pw is None:
                pw = self.rest_wq[p] if p >= 0 else one
            k = self.node_bone.get(int(i))
            if k is not None:
                w = V.qmul(D[k], self.rest_wq[i])
                q[i] = V.qmul(V.qconj(pw), w)
                Wq[i] = w
            else:
                Wq[i] = V.qmul(pw, q[i])
        p = m.parent[self.hips]
        pw = Wq.get(p, one) if p >= 0 else one
        tr[self.hips] = m.rest_t[self.hips] + _qrot(V.qconj(pw), hp * self.hip_h)
        return q, tr

    def skin_parts(self, q, tr, expr_w=None):
        local = self.m.local_mats(q, tr)
        world = self.m.world(local).astype(np.float32)
        out = []
        for p, p1 in zip(self.m.parts, self.p1):
            mats = (world[p["skin_nodes"]] @ p["ibm"])[:, :3, :]
            p_in = p1
            morphs = p.get("morphs")
            if expr_w is not None and morphs:
                delta = None
                for ei, d_arr in morphs.items():
                    w_e = float(expr_w[ei])
                    if w_e > 1e-4:
                        add_d = w_e * d_arr
                        delta = add_d.copy() if delta is None else (delta + add_d)
                if delta is not None:
                    p_in = p1.copy()
                    p_in[:, :3] += delta
            pos = np.zeros((len(p_in), 3), np.float32)
            for k in range(4):
                w = p["ww"][:, k]
                if not w.any():
                    continue
                pos += w[:, None] * np.einsum("nij,nj->ni", mats[p["slot"][:, k]], p_in)
            if self.yaw_sign < 0.0:
                pos[:, 0] = -pos[:, 0]
                pos[:, 2] = -pos[:, 2]
            out.append(pos)
        return out
