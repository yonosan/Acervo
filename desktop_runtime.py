"""Bandeja do Windows e instância única; nenhum serviço ou início automático."""
import ctypes
from ctypes import wintypes
import hashlib
from pathlib import Path
import threading
import time
from i18n import translate


class SingleInstance:
    def __init__(self, directory):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        for name, arguments, result in (
            ('CreateMutexW', [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            ('CreateEventW', [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            ('OpenEventW', [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
            ('SetEvent', [wintypes.HANDLE], wintypes.BOOL),
            ('WaitForSingleObject', [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            ('CloseHandle', [wintypes.HANDLE], wintypes.BOOL),
        ):
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result
        key = hashlib.sha256(str(Path(directory).resolve()).casefold().encode()).hexdigest()[:32]
        name = 'Local\\Acervo-' + key
        self.mutex = self.kernel.CreateMutexW(None, False, name)
        if not self.mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        self.existing = ctypes.get_last_error() == 183
        self.event = None
        if self.existing:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not self.event:
                self.event = self.kernel.OpenEventW(0x0002, False, name + '-show')
                if not self.event:
                    time.sleep(.05)
            if not self.event:
                self.close()
                raise RuntimeError(translate('O Acervo já está iniciando. Aguarde e tente abrir novamente.'))
            self.wake()
        else:
            self.event = self.kernel.CreateEventW(None, False, False, name + '-show')
            if not self.event:
                self.close()
                raise ctypes.WinError(ctypes.get_last_error())

    def wake(self):
        if self.event:
            self.kernel.SetEvent(self.event)

    def wait(self):
        return self.kernel.WaitForSingleObject(self.event, 0xffffffff) == 0

    def close(self):
        for name in ('event', 'mutex'):
            handle = getattr(self, name, None)
            if handle:
                self.kernel.CloseHandle(handle)
                setattr(self, name, None)


class BackgroundWindow:
    def __init__(self, window, app, instance, icon):
        self.window, self.app, self.instance, self.icon_path = window, app, instance, icon
        self.exiting = False
        self.tray = self.menu = self.icon = None
        self.watcher = None
        window.events.closing += self.on_closing
        window.events.minimized += self.on_minimized
        window.events.loaded += self.on_loaded

    def ui(self, callback, later=False):
        from System import Action
        form = self.window.native
        if form is None or form.IsDisposed:
            return
        if later:
            form.BeginInvoke(Action(callback))
        elif form.InvokeRequired:
            form.Invoke(Action(callback))
        else:
            callback()

    def start(self):
        if not self.window.events.shown.wait(30) or self.exiting:
            return
        try:
            self.ui(self.install_tray)
            self.watcher = threading.Thread(target=self.watch_open, daemon=True)
            self.watcher.start()
        except Exception:
            self.app.DESKTOP_RUNTIME['available'] = False
            self.app.event('segundo_plano_erro', 'Bandeja indisponível. A janela continuará fechando normalmente.')

    def install_tray(self):
        from System.Drawing import Icon, SystemIcons
        from System.Windows.Forms import ContextMenuStrip, NotifyIcon
        self.icon = Icon(str(self.icon_path)) if Path(self.icon_path).is_file() else SystemIcons.Application
        self.menu = ContextMenuStrip()
        self.menu.Items.Add('Abrir Acervo').Click += self.show
        self.menu.Items.Add('Sair do Acervo').Click += self.exit
        self.tray = NotifyIcon()
        self.tray.Icon, self.tray.Text, self.tray.ContextMenuStrip = self.icon, 'Acervo — abrir com dois cliques', self.menu
        self.tray.DoubleClick += self.show
        self.apply_language_ui()
        self.tray.Visible = True
        self.app.DESKTOP_RUNTIME['available'] = True
        self.app.event('bandeja_pronta', 'Bandeja disponível: Abrir Acervo ou Sair do Acervo. Fechar a janela pode manter os downloads.')

    def apply_language_ui(self):
        language = self.app.config_read('language') or 'en'
        self.window.native.Text = translate('Acervo — Música no seu PC', language)
        if self.menu:
            self.menu.Items[0].Text = translate('Abrir Acervo', language)
            self.menu.Items[1].Text = translate('Sair do Acervo', language)
        if self.tray:
            self.tray.Text = translate('Acervo — abrir com dois cliques', language)

    def update_language(self):
        self.ui(self.apply_language_ui)
        return True

    def watch_open(self):
        while not self.exiting and self.instance.wait():
            if not self.exiting:
                self.show()

    def on_closing(self):
        if not self.exiting and self.app.DESKTOP_RUNTIME['available'] and self.app.config_read('background_enabled') is not False:
            self.ui(self.hide_ui, later=True)
            return False
        self.dispose_tray()
        return True

    def on_minimized(self):
        if not self.exiting and self.app.DESKTOP_RUNTIME['available'] and self.app.config_read('background_enabled') is not False:
            self.hide()

    def on_loaded(self):
        if not self.exiting and self.app.DESKTOP_RUNTIME['background']:
            self.hide()

    def hide(self, *_):
        if self.exiting or not self.app.DESKTOP_RUNTIME['available']:
            return False
        self.ui(self.hide_ui)
        return True

    def hide_ui(self):
        if self.exiting:
            return
        browser = self.window.native.browser.webview
        core = browser.CoreWebView2
        if core:
            core.ExecuteScriptAsync('window.acervoBackground = true;')
        self.window.native.Hide()
        browser.Visible = False
        self.app.DESKTOP_RUNTIME.update(background=True)
        self.app.BACKGROUND.set()
        self.app.event('segundo_plano', 'Janela recolhida à bandeja. A fila continua; use Abrir Acervo para voltar ou Sair para encerrar.')
        self.set_memory_low(True)

    def set_memory_low(self, enabled):
        core = self.window.native.browser.webview.CoreWebView2
        if core:
            try:
                from Microsoft.Web.WebView2.Core import CoreWebView2MemoryUsageTargetLevel
                core.MemoryUsageTargetLevel = CoreWebView2MemoryUsageTargetLevel.Low if enabled else CoreWebView2MemoryUsageTargetLevel.Normal
                self.app.DESKTOP_RUNTIME['memory_low'] = enabled
                if enabled:
                    self.app.event('modo_economico', 'Interface oculta com meta de memória reduzida e consultas pausadas. A fila continua.')
            except Exception as exc:
                self.app.DESKTOP_RUNTIME['memory_low'] = False
                self.app.event('interface_oculta', 'Consultas da interface pausadas; a meta de memória não pôde ser alterada (' + type(exc).__name__ + ').')

    def show(self, *_):
        if self.exiting:
            return
        self.ui(self.show_ui)

    def show_ui(self):
        from System.Windows.Forms import FormWindowState
        form = self.window.native
        browser = form.browser.webview
        self.set_memory_low(False)
        browser.Visible = True
        self.app.DESKTOP_RUNTIME.update(background=False, memory_low=False)
        self.app.BACKGROUND.clear()
        form.WindowState = FormWindowState.Normal
        form.Show()
        form.Activate()
        form.BringToFront()
        if browser.CoreWebView2:
            browser.CoreWebView2.ExecuteScriptAsync("window.acervoBackground=false;window.dispatchEvent(new Event('acervo-resume'));")
        self.app.event('janela_restaurada', 'Janela restaurada a partir da instância existente; fila e biblioteca preservadas.')

    def exit(self, *_):
        if not self.exiting:
            self.exiting = True
            self.instance.wake()
            self.ui(self.exit_ui, later=True)

    def exit_ui(self):
        self.dispose_tray()
        self.window.native.Close()

    def dispose_tray(self):
        self.exiting = True
        self.instance.wake()
        self.app.DESKTOP_RUNTIME.update(available=False, background=False, memory_low=False)
        self.app.BACKGROUND.clear()
        if self.tray:
            self.tray.Visible = False
            self.tray.Dispose()
            self.tray = None
        if self.menu:
            self.menu.Dispose()
            self.menu = None

    def stop(self):
        self.exiting = True
        self.instance.wake()
        if self.watcher:
            self.watcher.join(timeout=2)
