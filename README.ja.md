# VRM RETRO WALK

[English](README.md)

自分の VRM アバター（0.x / 1.0）が、くっきりしたドット絵（Pyxel 3）になって、デスクトップの上を歩くマスコットです。歩く、走る、座る、おひるね、首かしげ、手を振る、なでると喜ぶ、つまみ上げる、ほかのウィンドウの上に乗る、などをします。

読み込みも表示も、すべて自分のパソコンの中で動きます。ファイルや画面の情報はどこにも送られません。

## できること

- **VRM を落とすだけ**: `.vrm` / `.glb` をキャラの上にドロップすると、そのアバターに変わります（右クリック → `.VRM を開く` でも可）。最後に読み込んだアバターは、次に起動したときも使われます。
- **なでる・つまむ**: 頭の上でマウスを左右にこするとなでられて喜びます。左ドラッグでつまみ上げ、空中で離すと落ちて着地します。ダブルクリックで駆け出します。
- **窓乗り**: ほかのアプリのウィンドウの上辺あたりへ運ぶと、その上に乗って腰かけたり歩いたりします。ウィンドウを動かすとついてきます。
- **大きさ**: マウスホイール（Mac は 2 本指のピンチ）や右上の `⤢` で自由に変えられます。`Shift + ダブルクリック` か右クリックの `BIG` で大きく表示します。
- **見た目**: `CRISP`（標準）/ `GB` / `AMBER`、1px のフチ線の ON / OFF。
- **余白透過**: キャラの周りの透明な部分をクリックすると、後ろのウィンドウを操作できます（右クリックで ON / OFF）。
- そのほか右クリックで、動きの指定、音の ON / OFF、最前面（`PIN`）、中央へ、終了。

## ライセンスの確認

改変を禁止しているアバターは読み込みません。VRM 1.0 の `modification` が `prohibited`（または項目なし）、VRM 0.x の `Redistribution_Prohibited` と `*_ND` のライセンスです。それ以外のアバターも、そのアバターのライセンスに従って使ってください。

## 手元で動かす（開発用）

Node.js、Rust、Python 3 が必要です。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt     # Windows は .venv\Scripts\pip
npm install
npm run dev
```

アプリは、同梱のエンジン（`src-tauri/engine_dist/`）がなければ、このフォルダの `.venv` の Python を使います。

## 配布用の Windows 版を作る

Python の入っていない Windows でも動くように、エンジン（Python と Pyxel）を PyInstaller の `--onedir` でまとめて同梱します。

- 手元で: `npm run pack:engine` のあと `npm run build:standalone`
- GitHub で: Actions の「Build VRM RETRO WALK (Windows Standalone)」を手動で実行すると、インストーラ（`.exe` / `.msi`）ができます。

**Windows の警告について**: このアプリには署名（コード署名証明書）がないので、初めて開くときに Windows SmartScreen が「Windows によって PC が保護されました」と表示することがあります。「詳細情報」→「実行」で起動できます。

## ファイル

| ファイル | 中身 |
|---|---|
| `src-tauri/` | 透明なウィンドウ、ドロップ、ウィンドウの位置の取得、エンジンとのやりとり（Tauri v2 / Rust） |
| `engine/walk_engine.py` | Pyxel 3（画面なしモード）で描いて、1 コマずつアプリに渡すエンジン |
| `engine/procedural_rig.py` | 歩く・座るなどの動きを計算で作る部分 |
| `engine/vrm_io.py` | VRM の読み込みとライセンスの確認 |
| `web/index.html` | 表示と操作、右クリックメニュー、効果音 |
| `assets/sample.vrm` | 最初に出てくるアバター |
| `scripts/build_engine.py` | 配布用のエンジンをまとめる |

## クレジット

- `assets/sample.vrm`: 千駄ヶ谷 渋（VRoid の CC0 サンプルモデル、VRM データは coati 氏）。VRM RETRO VIEWER 用に軽くしたもの
- [Pyxel](https://github.com/kitao/pyxel)（Takashi Kitao 氏、MIT License）、Tauri（MIT / Apache-2.0）、NumPy（BSD-3-Clause）、Pillow（MIT-CMU）、Python（PSF-2.0）。詳しくは [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)

## ライセンス

MIT License（haruka_apps）。LICENSE を見てください。

このアプリは「現状のまま」提供され、いかなる保証もありません。読み込む VRM のライセンスを守るのは使う人の責任です。
