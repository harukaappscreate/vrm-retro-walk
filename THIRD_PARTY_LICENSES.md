# Third-Party Licenses & Attributions (VRM RETRO WALK)

| Component | Role | License |
|---|---|---|
| **Python** | Bundled script runtime (`PyInstaller --onedir`) | Python Software Foundation License (PSF-2.0) |
| **Pyxel** (`kitao/pyxel`) | 256-color palette, `pyxel.cube` 3D renderer, `headless=True` framebuffer | MIT License |
| **NumPy** | Matrix/quaternion math, mesh skinning, palette LUT indexing | BSD-3-Clause License |
| **Pillow (PIL)** | PNG/JPEG texture decoding inside VRM/GLB files | MIT-CMU License |
| **Tauri** | Native frameless transparent desktop window & tray shell | MIT / Apache-2.0 |
| **Sendagaya Shibu** (`sample.vrm`) | Default built-in humanoid VRM mascot | CC0 1.0 Universal (Public Domain Dedication) |

## User VRM Safety Rule

User-supplied `.vrm` / `.glb` files are inspected at load time via `modification_allowed()` in `engine/vrm_io.py`. Models whose license forbids modification (VRM 1.0: `modification` is `"prohibited"` or missing; VRM 0.x: `Redistribution_Prohibited` or any `*_ND` license such as `CC_BY_ND`) are automatically refused.
