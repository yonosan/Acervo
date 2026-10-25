"""HTTPS com DNS validado e conexão fixada ao IP público verificado."""
import http.client
import ipaddress
import select
import socket
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, urljoin


class SourceError(ValueError):
    pass


def validate_url(url):
    try:
        p = urlsplit(url)
        if (p.scheme != 'https' or not p.hostname or p.username or p.password
                or p.port not in (None, 443) or '\\' in url or len(url) > 4096
                or any(ord(c) < 33 for c in url)):
            raise ValueError()
        p.hostname.encode('idna')
    except (ValueError, UnicodeError):
        raise SourceError('Use um link HTTPS público, sem senha e na porta padrão.')
    return p


def public_addresses(host):
    try:
        records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise SourceError('Não foi possível resolver o endereço da fonte.')
    addresses = list(dict.fromkeys(r[4][0] for r in records))
    def allowed(value):
        address = ipaddress.ip_address(value)
        return (address.is_global and not address.is_multicast and not address.is_reserved
                and not address.is_unspecified and not address.is_loopback and not address.is_link_local
                and not (address.version == 6 and (address.sixtofour or address.teredo or address.ipv4_mapped))
                and not (address.version == 4 and address in ipaddress.ip_network('192.0.0.0/24')))
    if not addresses or not all(allowed(a) for a in addresses):
        raise SourceError('Endereços internos, locais ou reservados são bloqueados.')
    return addresses


def public_socket(host, timeout=20):
    # Connect by numeric IP: a second DNS lookup cannot change the destination.
    addresses = public_addresses(host)
    for address in addresses:
        try:
            return socket.create_connection((address, 443), timeout)
        except OSError:
            continue
    raise SourceError('A fonte não respondeu à conexão segura.')


class PinnedHTTPS(http.client.HTTPSConnection):
    def connect(self):
        raw = public_socket(self.host, self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def open_public(url, method='GET', redirects=4, timeout=20):
    """No cookies, credentials, environment proxies or automatic redirects."""
    for _ in range(redirects + 1):
        p = validate_url(url)
        conn = PinnedHTTPS(p.hostname, timeout=timeout, context=ssl.create_default_context())
        try:
            path = p.path or '/'
            if p.query:
                path += '?' + p.query
            conn.request(method, path, headers={'User-Agent': 'Acervo/1.0 (personal audio)', 'Accept-Encoding': 'identity'})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location')
                conn.close()
                if not location:
                    raise SourceError('Redirecionamento inválido.')
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise SourceError(f'A fonte respondeu HTTP {response.status}; acesso restrito não será contornado.')
            return conn, response
        except Exception:
            conn.close()
            raise
    raise SourceError('Muitos redirecionamentos na fonte.')


class Tunnel(BaseHTTPRequestHandler):
    """Private, authenticated CONNECT proxy used only by the yt-dlp subprocess."""
    timeout = 20

    def do_CONNECT(self):
        if self.headers.get('Proxy-Authorization') != self.server.authorization:
            self.send_error(407)
            return
        upstream = None
        try:
            host, port = self.path.rsplit(':', 1)
            host = host.strip('[]').lower()
            if port != '443' or not any(host == d or host.endswith('.' + d) for d in ('youtube.com', 'youtu.be', 'googlevideo.com', 'ytimg.com', 'youtube-nocookie.com', 'google.com')):
                raise SourceError('Host fora das fontes do motor.')
            upstream = public_socket(host)
            self.send_response(200, 'Connection established')
            self.end_headers()
            deadline = time.monotonic() + 900
            while time.monotonic() < deadline:
                ready, _, _ = select.select([self.connection, upstream], [], [], 15)
                if not ready:
                    continue
                for source in ready:
                    chunk = source.recv(65536)
                    if not chunk:
                        return
                    (upstream if source is self.connection else self.connection).sendall(chunk)
        except (OSError, ValueError):
            return
        finally:
            if upstream:
                upstream.close()
            self.close_connection = True

    def log_message(self, *_):
        pass


def start_proxy():
    import base64
    import secrets
    password = secrets.token_hex(24)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Tunnel)
    server.daemon_threads = True
    server.authorization = 'Basic ' + base64.b64encode(f'acervo:{password}'.encode()).decode()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f'http://acervo:{password}@127.0.0.1:{server.server_port}'
