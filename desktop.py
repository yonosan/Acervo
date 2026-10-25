"""Entrada do aplicativo portátil Windows. A execução interna é gerenciada aqui."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time
from i18n import translate
from urllib.parse import urlsplit

ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
RESOURCES = Path(getattr(sys, '_MEIPASS', ROOT))


def restore_worker_pipes():
    if os.name != 'nt':
        return
    import msvcrt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    for name, identifier, mode in (('stdin', -10, 'r'), ('stdout', -11, 'w')):
        if getattr(sys, name) is None:
            handle = kernel.GetStdHandle(identifier & 0xffffffff)
            if not handle or handle == -1:
                raise RuntimeError('Canal interno de mídia indisponível.')
            descriptor = msvcrt.open_osfhandle(handle, os.O_BINARY | (os.O_RDONLY if mode == 'r' else os.O_WRONLY))
            stream = os.fdopen(descriptor, mode, encoding='utf-8', buffering=1)
            setattr(sys, name, stream)
        elif hasattr(getattr(sys, name), 'reconfigure'):
            getattr(sys, name).reconfigure(encoding='utf-8')
    if sys.stderr is None:
        sys.stderr = open(os.devnull, 'w')


def main():
    os.environ['PATH'] = str(RESOURCES / 'bin') + os.pathsep + os.environ.get('PATH', '')
    node = RESOURCES / 'bin' / 'node.exe'
    if node.exists():
        os.environ['PLAYWRIGHT_NODEJS_PATH'] = str(node)
    os.environ['YTDLP_NO_PLUGINS'] = '1'
    if '--acervo-engine' in sys.argv:
        restore_worker_pipes()
        import yt_engine
        try:
            yt_engine.main()
        except Exception as exc:
            yt_engine.emit({'error': str(exc) if isinstance(exc, ValueError) else 'Não foi possível obter o áudio do YouTube. A origem pode ter recusado o acesso; não serão usados desvios de restrições.'})
            return 1
        return 0

    data = Path(os.environ.get('ACERVO_DESKTOP_DATA', str(ROOT / 'Dados'))).resolve()
    data.mkdir(parents=True, exist_ok=True)
    from desktop_runtime import SingleInstance
    instance = SingleInstance(data)
    try:
        if instance.existing:
            return 0
        return run_desktop(data, instance)
    finally:
        instance.close()


def run_desktop(data, instance):
    if sys.stderr is None:
        sys.stderr = (data / 'inicializacao.log').open('a', encoding='utf-8', buffering=1)
    if sys.stdout is None:
        sys.stdout = sys.stderr
    ticket = secrets.token_urlsafe(48)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    os.environ.update(APP_PASSWORD=secrets.token_urlsafe(48), APP_ORIGIN=origin, DATA_DIR=str(data),
                      ACERVO_DESKTOP='1', ACERVO_BOOT_TICKET=ticket)
    import app
    import uvicorn
    config = uvicorn.Config(app.app, host='127.0.0.1', port=port, workers=1, access_log=False,
                            limit_concurrency=32, timeout_keep_alive=5, log_level='warning')
    server = uvicorn.Server(config)
    runner = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    runner.start()
    deadline = time.monotonic() + 30
    while not server.started and runner.is_alive() and time.monotonic() < deadline:
        time.sleep(.05)
    if not server.started:
        raise RuntimeError('O Acervo não conseguiu iniciar. Consulte Dados/inicializacao.log.')
    if not app.config_read('output_folder'):
        output = ROOT / 'Músicas'
        output.mkdir(exist_ok=True)
        app.config_write('output_folder', str(output))
    app.event('desktop_inicio', 'Acervo Windows aberto. Execução automática e arquivos salvos diretamente no computador.')

    import webview
    webview.settings['ALLOW_FILE_URLS'] = False
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = True
    webview.settings['ALLOW_DOWNLOADS'] = False

    class Bridge:
        def _check(self, token):
            current = urlsplit(window.get_current_url() or '')
            if not secrets.compare_digest(str(token), webview.token) or (current.scheme + '://' + current.netloc) != origin:
                raise RuntimeError('Solicitação fora da janela do Acervo.')

        def choose_folder(self, token):
            self._check(token)
            chosen = window.create_file_dialog(webview.FileDialog.FOLDER, directory=app.config_read('output_folder'))
            if chosen:
                folder = str(Path(chosen[0]).resolve())
                app.config_write('output_folder', folder)
                app.event('pasta_escolhida', 'Pasta de destino atualizada pelo usuário.')
                return folder
            return None

        def open_folder(self, identifier, token):
            self._check(token)
            item = app.get('job', str(identifier))
            path = Path(item.get('saved_path', ''))
            if not item.get('saved_path') or not path.is_file():
                raise RuntimeError('O arquivo salvo não foi encontrado. Ele pode ter sido movido.')
            os.startfile(str(path.parent))
            return True

        def pick_move_folder(self, token):
            self._check(token)
            chosen = window.create_file_dialog(webview.FileDialog.FOLDER, directory=app.config_read('move_folder') or app.config_read('output_folder'))
            if chosen:
                folder = str(Path(chosen[0]).resolve())
                app.config_write('move_folder', folder)
                return folder
            return None

        def open_logs(self, token):
            self._check(token)
            os.startfile(str(data / 'logs'))
            return True

        def hide_window(self, token):
            self._check(token)
            return background.hide()

        def exit_app(self, token):
            self._check(token)
            background.exit()
            return True

        def update_language(self, token):
            self._check(token)
            return background.update_language()

    window = webview.create_window(translate('Acervo — Música no seu PC', app.config_read('language') or 'en'), origin + '/desktop/boot/' + ticket,
                                    js_api=Bridge(), width=1280, height=900, min_size=(880, 650),
                                    background_color='#101216', text_select=True)
    from desktop_runtime import BackgroundWindow
    background = BackgroundWindow(window, app, instance, RESOURCES / 'static' / 'acervo.ico')
    try:
        webview.start(background.start, gui='edgechromium', debug=False, private_mode=True, storage_path=str(data / 'janela'))
    finally:
        background.stop()
        server.should_exit = True
        runner.join(timeout=60)
        sock.close()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        ctypes.windll.user32.MessageBoxW(None, translate(str(exc)) + '\n\n' + translate('Se o componente de janela estiver ausente, instale o Microsoft Edge WebView2 Runtime pelo site oficial da Microsoft.'), translate('Acervo — Não foi possível abrir'), 0x10)
        raise
