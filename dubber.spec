# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Dubber Bee — Chinese → Vietnamese video dubbing app
#
# Build:  scripts\build_exe.ps1        (Windows) → dist\Dubber\Dubber.exe
#         scripts/build_exe_mac.sh      (macOS)   → dist/Dubber.app
# One-directory bundle, ~3 GB (models ~1.8 GB)
#
# What's included:
#   - dubber Python package
#   - PySide6 (Qt GUI)
#   - sherpa-onnx + DLLs (ASR)
#   - piper + espeak-ng-data (TTS)
#   - groq + httpx (online translation)
#   - torch (CPU) + transformers + sentencepiece (offline translation)
#   - audio-separator (MDX-Net music/voice separation) + librosa/scipy
#   - VieNeu-TTS SDK (ONNX path) + sea-g2p
#   - FFmpeg/FFprobe binaries (in resources/)
#   - ASR models, FSMN-VAD, Silero VAD, piper voices, VieNeu models, UVR MDX-Net model
#     (in resources/models/)
#
# Note: HuggingFace models (MarianMT ~300MB, NLLB 1.3B ~5.5GB) download on first use
#       and are cached in ~/.cache/huggingface (Windows: %USERPROFILE%\.cache\huggingface)
#
# What's NOT included:
#   - torchaudio
#   - gradio (VieNeu's web UI only)

import importlib.util
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, copy_metadata


def _installed(pkg: str) -> bool:
    """Optional packages (pyvi is skipped on Intel Macs) are bundled only when present."""
    return importlib.util.find_spec(pkg) is not None

block_cipher = None
project_root = Path(SPECPATH)

# ── Collect packages ───────────────────────────────────────────────────────────
sherpa_datas,       sherpa_binaries,       sherpa_hiddenimports       = collect_all('sherpa_onnx')
piper_datas,        piper_binaries,        piper_hiddenimports        = collect_all('piper')
torch_datas,        torch_binaries,        torch_hiddenimports        = collect_all('torch')
transformers_datas, transformers_binaries, transformers_hiddenimports = collect_all('transformers')

# Separation (audio-separator) + VieNeu-TTS and their data files / native libs
extra_datas, extra_binaries, extra_hiddenimports = [], [], []
for _pkg in ('audio_separator', 'torchvision', 'vieneu', 'vieneu_utils', 'sea_g2p', 'librosa', 'soxr',
             'kaldi_native_fbank', 'keyring', 'pyvi', 'pycrfsuite', 'sklearn_crfsuite'):
    if not _installed(_pkg):
        print(f'[dubber.spec] {_pkg} not installed — not bundled')
        continue
    _d, _b, _h = collect_all(_pkg)
    extra_datas += _d; extra_binaries += _b; extra_hiddenimports += _h
# keyring finds its OS backends (Keychain / Credential Manager) through entry points
extra_datas += copy_metadata('keyring')
IS_WIN = sys.platform == 'win32'
if IS_WIN:
    extra_hiddenimports += ['keyring.backends.Windows', 'win32ctypes.core']

# PySide6: only the Qt modules the app imports (PyInstaller's own hooks collect their
# plugins). collect_all('PySide6') pulled every Qt module — WebEngine, 3D, QML… ~450 MB.
QT_MODULES = ['PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets', 'PySide6.QtMultimedia']

# Explicitly add sherpa-onnx DLLs (belt-and-suspenders — collect_all may miss them)
import sherpa_onnx as _sp
_sp_dir = Path(_sp.__file__).parent
extra_sherpa_bins = [(str(f), '.') for f in _sp_dir.glob('*.dll')]

# ── FFmpeg binaries from resources/ ───────────────────────────────────────────
IS_MAC = sys.platform == 'darwin'
ffmpeg_bins = []
for _name in (['ffmpeg', 'ffprobe'] if IS_MAC else ['ffmpeg.exe', 'ffprobe.exe']):
    _p = project_root / 'resources' / _name
    if _p.is_file():
        ffmpeg_bins.append((str(_p), 'resources'))

# macOS: self-contained ffmpeg-full (bin/ + lib/, built by build_exe_mac.sh).
# Added as DATA so PyInstaller doesn't merge its dylibs into Frameworks/.
# Subtitle fonts (dubber/assets/fonts → same path in the bundle; see fonts.fonts_dir())
font_datas = [(str(_f), 'dubber/assets/fonts') for _f in (project_root / 'dubber' / 'assets' / 'fonts').glob('*.ttf')]
# App logo (header + Dock / taskbar icon at runtime; see gui.app_logo)
logo_dir = project_root / 'dubber' / 'assets' / 'logo'
# logo_1024.png (macOS Dock icon) stays out of the Windows bundle: it uses icon.ico, and the
# Windows zip sits just under GitHub's 2 GiB release-asset limit
font_datas += [(str(_f), 'dubber/assets/logo') for _f in logo_dir.glob('logo_*.png')
               if IS_MAC or _f.name != 'logo_1024.png']

ffmpeg_mac_datas = []
_ffmac = project_root / 'resources' / 'ffmpeg-mac'
if IS_MAC and _ffmac.exists():
    for _f in _ffmac.rglob('*'):
        if _f.is_file():
            _rel = _f.parent.relative_to(project_root)
            ffmpeg_mac_datas.append((str(_f), str(_rel)))

# ── Model data files from resources/models/ ───────────────────────────────────
model_datas = []
_models_root = project_root / 'resources' / 'models'
if _models_root.exists():
    for _f in _models_root.rglob('*'):
        if _f.is_file():
            _rel = _f.parent.relative_to(project_root / 'resources')
            _dest = 'resources/' + str(_rel).replace('\\', '/')
            model_datas.append((str(_f), _dest))

# ── Analysis ───────────────────────────────────────────────────────────────────
a = Analysis(
    ['dubber/__main__.py'],
    pathex=[str(project_root)],
    binaries=(sherpa_binaries + extra_sherpa_bins + piper_binaries
              + ffmpeg_bins + torch_binaries + transformers_binaries + extra_binaries),
    datas=(sherpa_datas + piper_datas + model_datas + ffmpeg_mac_datas + font_datas
           + torch_datas + transformers_datas + extra_datas),
    hiddenimports=[
        # Dynamic imports in pipeline / translators
        'dubber.core.translate.groq_translator',
        'dubber.core.translate.local_translator',
        'dubber.core.translate.gemini',
        'dubber.core.audio.separate',
        'dubber.core.asr.model_manager',
        'dubber.core.asr.fsmn_vad', 'dubber.core.asr._fsmn_e2e_vad',
        'kaldi_native_fbank', 'yaml',
        # HTTP / networking (groq client deps)
        'httpx', 'httpcore', 'anyio', 'anyio._backends._asyncio',
        'anyio._backends._trio', 'sniffio', 'certifi',
        'charset_normalizer', 'idna', 'h11',
        # Offline translation
        'transformers', 'transformers.models.marian',
        'transformers.models.nllb', 'tokenizers', 'sentencepiece',
        'torch', 'torch.nn', 'torch.jit', 'torch.utils', 'torch.utils.data',
        # Subtitles: text measuring (Pillow) + Vietnamese word segmentation (pyvi → CRF
        # model pickles in pyvi/models, collected above; works without sklearn)
        'PIL', 'PIL.ImageFont',
        *([ 'pyvi', 'pyvi.ViTokenizer', 'sklearn_crfsuite', 'pycrfsuite', 'tabulate']
          if _installed('pyvi') else []),
        # Other
        'platformdirs', 'soundfile', 'resampy', 'cffi', 'pycparser',
        *sherpa_hiddenimports,
        *piper_hiddenimports,
        *QT_MODULES,
        *torch_hiddenimports,
        *transformers_hiddenimports,
        *extra_hiddenimports,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # torchaudio isn't needed (torchvision is: audio-separator → onnx2torch imports it)
        'torchaudio',
        # VieNeu's Gradio web UI — the app only uses its ONNX inference path
        'gradio', 'gradio_client', 'soundstretch',
        # Dev / test tools never needed at runtime (PIL + pandas stay: torchvision and
        # audio-separator import them)
        'pytest', 'IPython', 'matplotlib',
        'notebook', 'sphinx', 'sklearn', 'cv2',
        'tensorflow', 'jax',
        # Qt modules the app never uses (some packages import them optionally)
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtQml',
        'PySide6.QtQuick', 'PySide6.Qt3DCore', 'PySide6.QtPdf', 'PySide6.QtCharts',
        'PySide6.QtDesigner', 'PyQt5', 'PyQt6', 'tkinter',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

_icon = logo_dir / ('icon.icns' if IS_MAC else 'icon.ico')   # the bee logo

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Dubber',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # No terminal window (windowed GUI app)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(_icon) if _icon.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,   # UPX corrupts torch / onnxruntime / Qt DLLs
    name='Dubber',
)

# ── macOS: wrap the onedir bundle into Dubber.app ─────────────────────────────
if IS_MAC:
    app = BUNDLE(
        coll,
        name='Dubber.app',
        icon=str(_icon) if _icon.exists() else None,
        bundle_identifier='com.dubber.app',
        info_plist={
            'CFBundleName': 'Dubber Bee',          # menu bar / Dock name
            'CFBundleDisplayName': 'Dubber Bee',
            'CFBundleShortVersionString': '0.2.0',
            'NSHighResolutionCapable': True,
            'LSMinimumSystemVersion': '12.0',
        },
    )
