"""Compila a pasta portátil sem alterar instalações ou dados existentes."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parent
args = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onedir', '--windowed', '--name', 'Acervo',
        '--distpath', str(root / 'dist'), '--workpath', str(root / 'build'), '--specpath', str(root / 'build'),
        '--paths', str(root), '--add-data', str(root / 'static') + os.pathsep + 'static',
        '--collect-all', 'yt_dlp', '--collect-all', 'yt_dlp_ejs', '--collect-data', 'webview', '--collect-all', 'playwright',
        '--hidden-import', 'webview.platforms.winforms', '--hidden-import', 'uvicorn.logging',
        '--hidden-import', 'uvicorn.loops.asyncio', '--hidden-import', 'uvicorn.protocols.http.h11_impl',
        '--hidden-import', 'uvicorn.protocols.websockets.websockets_impl', '--hidden-import', 'uvicorn.lifespan.on',
        '--copy-metadata', 'yt-dlp-ejs', '--exclude-module', 'PyQt5', '--exclude-module', 'PyQt6',
        '--exclude-module', 'PySide2', '--exclude-module', 'PySide6', '--exclude-module', 'tkinter', '--noupx',
        '--icon', str(root / 'static/acervo.ico')]
for name in ('ffmpeg', 'ffprobe', 'node'):
    binary = shutil.which(name)
    if not binary:
        raise SystemExit(f'Instale {name} e deixe-o disponível no PATH antes de compilar.')
    args.extend(['--add-binary', binary + os.pathsep + 'bin'])
args.append(str(root / 'desktop.py'))
subprocess.run(args, check=True)
duplicate = root / 'dist/Acervo/_internal/playwright/driver/node.exe'
if duplicate.is_file():
    duplicate.unlink()
for name in ('LICENSE', 'README.md', 'MANUAL.md', 'THIRD_PARTY_NOTICES.md', 'SOURCES.md'):
    shutil.copy2(root / name, root / 'dist/Acervo' / name)
print('Compilação pronta em dist/Acervo. Mantenha Acervo.exe e _internal juntos.')
