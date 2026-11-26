# VRM RETRO WALK

[日本語](README.ja.md)

A desktop mascot: your own VRM avatar (0.x / 1.0) becomes crisp pixel art (Pyxel 3) and walks around your desktop. It walks, runs, sits, naps, tilts its head, waves, smiles when you pet it, can be picked up, and can ride on top of other windows.

Everything runs on your own computer. Your files and your screen are never sent anywhere.

## Features

- **Drop a VRM**: drop a `.vrm` / `.glb` on the character to switch to that avatar (or right-click → `.VRM を開く`). The last avatar you loaded is used again next time.
- **Pet and pick up**: rub the mouse left and right over the head to pet it. Left-drag to pick it up; let go in the air and it falls and lands. Double-click to make it run.
- **Window riding**: carry it near the top edge of another app's window and it sits or walks on top of it, and follows the window when you move it.
- **Size**: mouse wheel (pinch on a Mac trackpad) or the `⤢` handle. `Shift + double-click` or `BIG` in the right-click menu for a big view.
- **Look**: `CRISP` (default) / `GB` / `AMBER`, 1px outline on / off.
- **Click-through margins**: clicks on the transparent area around the character go to the window behind it (on / off in the right-click menu).
- Also in the right-click menu: motion, sound on / off, always on top (`PIN`), center, quit.

## License check

Avatars whose license forbids modification are refused: VRM 1.0 `modification: prohibited` (or missing), VRM 0.x `Redistribution_Prohibited` and the `*_ND` licenses. For other avatars, follow their license.

## Run it yourself (development)

You need Node.js, Rust and Python 3.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt     # Windows: .venv\Scripts\pip
npm install
npm run dev
```

Without the bundled engine (`src-tauri/engine_dist/`), the app uses the Python in this folder's `.venv`.

## Build the Windows version

The engine (Python + Pyxel) is bundled with PyInstaller `--onedir`, so it runs on Windows without Python installed.

- Locally: `npm run pack:engine`, then `npm run build:standalone`
- On GitHub: run the "Build VRM RETRO WALK (Windows Standalone)" action by hand to get the installers (`.exe` / `.msi`).

**Windows warning**: the app is not code-signed, so Windows SmartScreen may show "Windows protected your PC" the first time. Click "More info" → "Run anyway".

## Credits

- `assets/sample.vrm`: Sendagaya Shibu, a CC0 sample model of VRoid (VRM data by coati), made smaller for VRM RETRO VIEWER
- [Pyxel](https://github.com/kitao/pyxel) by Takashi Kitao (MIT License), Tauri (MIT / Apache-2.0), NumPy (BSD-3-Clause), Pillow (MIT-CMU), Python (PSF-2.0). See [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)

## License

MIT License (haruka_apps). See LICENSE.

This app is provided "AS IS", without warranty of any kind. You are responsible for following the license of the VRM you load.
