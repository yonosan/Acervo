import hashlib
import hmac
import json
import logging
import mimetypes
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import tempfile
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit, parse_qs, urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from catalog import AUDIO_EXT, archive_item, archive_search, match, parse_import
from security import SourceError, open_public, validate_url, start_proxy
import covers
from i18n import CATALOG

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get('DATA_DIR', str(BASE / 'data'))).resolve()
ORIGIN = os.environ.get('APP_ORIGIN', 'http://127.0.0.1:8765').rstrip('/')
PASSWORD = os.environ.get('APP_PASSWORD', '')
DESKTOP = os.environ.get('ACERVO_DESKTOP') == '1'
BOOT_TICKET = os.environ.get('ACERVO_BOOT_TICKET', '')
SPOTIFY = None
MAX_BYTES = int(os.environ.get('MAX_FILE_MB', '100')) * 1024 * 1024
MAX_DISK = int(os.environ.get('MAX_DISK_MB', '1024')) * 1024 * 1024
TTL = int(os.environ.get('FILE_TTL_HOURS', '2')) * 3600
WORKERS = max(1, min(2, int(os.environ.get('DOWNLOAD_WORKERS', '2'))))
QUEUE_LIMIT = 5000 if DESKTOP else 20
ACTIVE = {'Na fila', 'Baixando', 'Convertendo', 'Cancelando'}
STATE = threading.RLock()
NETWORK = threading.BoundedSemaphore(3)
JOBS = queue.Queue(maxsize=QUEUE_LIMIT)
SESSIONS = {}
RATES = {}
STOP = threading.Event()
BACKGROUND = threading.Event()
DESKTOP_RUNTIME = {'available': False, 'background': False, 'memory_low': False}
PROXY_URL = ''
PROXY = None
DB = None
COVER_TASK = {'running': False, 'done': 0, 'total': 0, 'embedded': 0, 'unavailable': 0}
FILES = threading.RLock()
MOVE_CANCEL = threading.Event()
MOVE_TASK = {'running': False, 'done': 0, 'total': 0, 'moved': 0, 'skipped': 0, 'errors': 0, 'cancelled': False, 'last_error': ''}
logger = logging.getLogger('acervo')


def now():
    return datetime.now(timezone.utc).isoformat()


def save(kind, item):
    with STATE:
        DB.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?)', (kind, item['id'], json.dumps(item, ensure_ascii=False), time.time()))
        DB.commit()
    return item


def records(kind):
    with STATE:
        return [json.loads(r[0]) for r in DB.execute('SELECT body FROM records WHERE kind=? ORDER BY updated DESC', (kind,))]


def get(kind, identifier):
    with STATE:
        row = DB.execute('SELECT body FROM records WHERE kind=? AND id=?', (kind, identifier)).fetchone()
    if not row:
        raise HTTPException(404, 'Registro não encontrado.')
    return json.loads(row[0])


def config_read(key):
    try:
        return get('config', key).get('value')
    except HTTPException:
        return None


def config_write(key, value):
    save('config', {'id': key, 'value': value})


def save_spotify_track(track):
    try:
        previous = get('track', track['id'])
        for key in ('state', 'candidates', 'media_id', 'job_id'):
            if key in previous:
                track[key] = previous[key]
        if not track.get('thumbnail') and previous.get('thumbnail'):
            track['thumbnail'] = previous['thumbnail']
    except HTTPException:
        pass
    save('track', track)


def event(action, message, job_id=None):
    item = {'id': secrets.token_hex(8), 'time': now(), 'action': action, 'message': message, 'job_id': job_id}
    logger.info(json.dumps(item, ensure_ascii=False))
    save('event', item)
    with STATE:
        DB.execute("DELETE FROM records WHERE kind='event' AND id NOT IN (SELECT id FROM records WHERE kind='event' ORDER BY updated DESC LIMIT 1000)")
        DB.commit()


def patch_job(identifier, **changes):
    with STATE:
        item = get('job', identifier)
        item.update(changes, updated=now())
        save('job', item)
        if item.get('item_id') and 'state' in changes:
            try:
                track = get('track', item['item_id'])
                if track.get('job_id') == identifier:
                    track['state'] = 'Na fila' if item['state'] in ACTIVE else 'Precisa revisar' if item['state'] == 'Cancelada' else item['state']
                    save('track', track)
            except HTTPException:
                pass
    return item


def disk_used():
    return sum(p.stat().st_size for p in (DATA / 'files').rglob('*') if p.is_file())


def check_space():
    if disk_used() + WORKERS * 2 * MAX_BYTES > MAX_DISK or shutil.disk_usage(DATA).free < 3 * MAX_BYTES:
        raise SourceError('Armazenamento temporário cheio. Aguarde a limpeza ou libere espaço.')


def terminate(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=10)
    else:
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=10)


def run_engine(request, job_id=None):
    request.update(proxy=PROXY_URL, max_bytes=MAX_BYTES, max_duration=1200)
    env = {**os.environ, 'YTDLP_NO_PLUGINS': '1', 'NO_PROXY': '', 'no_proxy': '', 'PYTHONIOENCODING': 'utf-8'}
    env.pop('APP_PASSWORD', None)
    process = subprocess.Popen([sys.executable, '--acervo-engine'] if getattr(sys, 'frozen', False) else [sys.executable, str(BASE / 'yt_engine.py')], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
                               env=env, start_new_session=os.name != 'nt', creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    messages = queue.Queue(maxsize=128)
    def read():
        try:
            for line in process.stdout:
                messages.put(json.loads(line))
        except (ValueError, OSError):
            pass
        finally:
            messages.put(None)
    threading.Thread(target=read, daemon=True).start()
    process.stdin.write(json.dumps(request) + '\n')
    process.stdin.close()
    deadline = time.monotonic() + (900 if job_id else 100)
    result = None
    try:
        while True:
            if time.monotonic() > deadline:
                raise SourceError('Tempo limite da fonte atingido. Tente novamente mais tarde.')
            if STOP.is_set() or (job_id and get('job', job_id).get('cancel')):
                raise InterruptedError()
            if job_id:
                folder = DATA / 'files' / job_id
                if sum(p.stat().st_size for p in folder.glob('*') if p.is_file()) > MAX_BYTES * (2 if request.get('video') else 1):
                    raise SourceError('Arquivo acima do limite de tamanho.')
            try:
                msg = messages.get(timeout=.2)
            except queue.Empty:
                continue
            if msg is None:
                break
            if 'error' in msg:
                raise SourceError(msg['error'])
            if 'result' in msg:
                result = msg['result']
            elif job_id:
                patch_job(job_id, progress=msg.get('progress'), bytes=msg.get('bytes', 0))
        process.wait(timeout=5)
        if process.returncode or result is None:
            raise SourceError('O motor não conseguiu obter a mídia.')
        return result
    finally:
        terminate(process)
        process.stdout.close()


def remember(media):
    return remember_many([media])[0]


def remember_many(items):
    results = []
    with STATE:
        for media in items:
            media['id'] = hashlib.sha256(media['url'].encode()).hexdigest()[:24]
            if not media.get('formats'):
                try:
                    previous = get('media', media['id'])
                    if previous.get('formats'):
                        media = previous
                except HTTPException:
                    pass
            media['title'] = str(media.get('title', 'Sem título'))[:300]
            media['artist'] = str(media.get('artist', 'Artista não informado'))[:300]
            results.append(media)
        DB.executemany('INSERT OR REPLACE INTO records VALUES (?,?,?,?)',
                       [('media', m['id'], json.dumps(m, ensure_ascii=False), time.time()) for m in results])
        DB.execute("DELETE FROM records WHERE kind='media' AND id NOT IN (SELECT id FROM records WHERE kind='media' ORDER BY updated DESC LIMIT 2000) AND id NOT IN (SELECT json_extract(body,'$.media_id') FROM records WHERE kind='job' AND json_extract(body,'$.media_id') IS NOT NULL AND json_extract(body,'$.state') IN ('Na fila','Baixando','Convertendo','Cancelando'))")
        DB.commit()
    return results


def analyze(url):
    p = validate_url(url)
    host = p.hostname.lower()
    if host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be'):
        playlist = parse_qs(p.query).get('list', [''])[0]
        if playlist:
            if not DESKTOP:
                raise SourceError('Playlists estão disponíveis no aplicativo Windows.')
            if not re.fullmatch(r'[A-Za-z0-9_-]{2,200}', playlist):
                raise SourceError('Identificador da playlist inválido.')
            url = 'https://www.youtube.com/playlist?' + urlencode({'list': playlist})
            return remember_many(run_engine({'action': 'playlist', 'url': url}))
        return [remember(run_engine({'action': 'analyze', 'url': url}))]
    if host in ('archive.org', 'www.archive.org') and p.path.startswith('/details/'):
        return [remember(m) for m in archive_item(p.path.split('/')[2])]
    ext = Path(unquote(p.path)).suffix.lower().lstrip('.')
    if ext not in AUDIO_EXT:
        raise SourceError('Fonte não suportada nesta versão. Use YouTube, item do Internet Archive ou link HTTPS direto de áudio.')
    conn, response = open_public(url)
    try:
        mime = response.getheader('Content-Type', '').split(';')[0].lower()
        if not mime.startswith('audio/') and mime not in ('application/ogg', 'application/octet-stream', 'video/webm'):
            raise SourceError('O endereço não respondeu com um arquivo de áudio compatível.')
        size = int(response.getheader('Content-Length', '0')) or None
        if size and size > MAX_BYTES:
            raise SourceError('Arquivo acima do limite de tamanho.')
        response.read(64)
    finally:
        conn.close()
    title = Path(unquote(p.path)).stem
    return [remember({'url': url, 'title': title, 'artist': 'Artista não informado', 'album': '',
                      'duration': None, 'source': 'Internet Archive' if host.endswith('archive.org') else 'Arquivo direto',
                      'formats': [{'id': 'original', 'ext': ext, 'abr': None, 'size': size}]})]


def download_direct(media, target, job_id):
    conn, response = open_public(media['url'])
    start = time.monotonic()
    try:
        total = int(response.getheader('Content-Length', '0')) or None
        if total and total > MAX_BYTES:
            raise SourceError('Arquivo acima do limite de tamanho.')
        written, last = 0, 0
        with target.open('xb') as output:
            while True:
                if STOP.is_set() or get('job', job_id).get('cancel'):
                    raise InterruptedError()
                if time.monotonic() - start > 900:
                    raise SourceError('Tempo limite do download atingido.')
                chunk = response.read(65536)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_BYTES:
                    raise SourceError('Arquivo acima do limite de tamanho.')
                output.write(chunk)
                if time.monotonic() - last > .5:
                    patch_job(job_id, bytes=written, progress=min(94, written / total * 94) if total else None)
                    last = time.monotonic()
        if not written or (total and written != total):
            raise SourceError('Download incompleto. O arquivo não foi liberado.')
    finally:
        conn.close()


def probe(path, video=False):
    result = subprocess.run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_streams', '-show_format', '-of', 'json', str(path)], capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if result.returncode:
        raise SourceError('O arquivo recebido não é um áudio válido.')
    data = json.loads(result.stdout)
    has_audio = any(s.get('codec_type') == 'audio' for s in data.get('streams', []))
    has_video = any(s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic') for s in data.get('streams', []))
    if not has_audio or has_video != video:
        raise SourceError('O arquivo não contém vídeo com áudio válido.' if video else 'A fonte não forneceu um arquivo somente de áudio.')
    if float(data.get('format', {}).get('duration') or 0) > 1200:
        raise SourceError('O áudio ultrapassa o limite de 20 minutos.')
    return data


def filename(artist, title, ext):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', artist + ' - ' + title).strip(' .')[:120]
    name = name.encode('utf-8')[:180].decode('utf-8', errors='ignore').rstrip(' .')
    return (name or 'Artista - Música') + '.' + ext


def save_to_folder(source, folder, name):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for number in range(1, 10000):
        candidate = folder / (name if number == 1 else f'{Path(name).stem} ({number}){Path(name).suffix}')
        try:
            output = candidate.open('xb')
        except FileExistsError:
            continue
        try:
            with output, source.open('rb') as incoming:
                shutil.copyfileobj(incoming, output)
                output.flush()
                os.fsync(output.fileno())
            return str(candidate.resolve())
        except Exception:
            candidate.unlink(missing_ok=True)
            raise
    raise SourceError('Há muitos arquivos com esse nome na pasta escolhida.')


def convert_media(source, directory, item, selected, image, duration):
    output = item['output']
    if output == 'original' or output == 'mp3' and source.suffix == '.mp3' and selected.get('kind', 'audio') == 'audio':
        return source
    patch_job(item['id'], state='Convertendo', progress=95)
    video_source = selected.get('kind', 'audio') == 'video'
    cover_video = output == 'mp4' and not video_source
    event('conversao_inicio', 'Criando vídeo MP4 com capa e áudio.' if cover_video else f'Preparando {output.upper()}; a conversão não melhora a gravação.', item['id'])
    converted = directory / ('converted.' + output)
    args = ['ffmpeg', '-nostdin', '-v', 'error', '-filter_threads', '1', '-protocol_whitelist', 'file,pipe']
    if cover_video:
        if not duration or duration <= 0:
            raise SourceError('Não foi possível medir a duração para criar o vídeo com capa.')
        if image:
            args += ['-loop', '1', '-framerate', '1', '-i', str(image)]
        else:
            args += ['-f', 'lavfi', '-i', 'color=c=0x181c22:s=1280x720:r=1']
        args += ['-protocol_whitelist', 'file,pipe', '-i', str(source), '-map', '0:v:0', '-map', '1:a:0',
                 '-vf', 'scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1',
                 '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'stillimage', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-t', str(duration), '-shortest']
    else:
        args += ['-i', str(source), '-map', '0:a:0']
        if output == 'mp3':
            args += ['-vn', '-c:a', 'libmp3lame']
            args += ['-q:a', '0'] if item['bitrate'] == 0 else ['-b:a', str(item['bitrate']) + 'k']
        else:
            args += ['-map', '0:v:0', '-c', 'copy'] if not selected.get('transcode') else [
                '-map', '0:v:0', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k']
    if output == 'mp4':
        args += ['-movflags', '+faststart']
    args += ['-threads', '2', '-metadata', 'artist=' + item['artist'], '-metadata', 'title=' + item['title'], str(converted)]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=os.name != 'nt', creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    deadline = time.monotonic() + 900
    try:
        while proc.poll() is None:
            if STOP.is_set() or get('job', item['id']).get('cancel'):
                raise InterruptedError()
            if time.monotonic() > deadline or converted.exists() and converted.stat().st_size > MAX_BYTES:
                raise SourceError('Conversão atingiu o limite de tempo ou tamanho do arquivo.')
            time.sleep(.15)
        if proc.returncode:
            raise SourceError('FFmpeg não conseguiu converter este arquivo.')
    finally:
        terminate(proc)
    probe(converted, video=output == 'mp4')
    if cover_video:
        patch_job(item['id'], video_from_audio=True)
    source.unlink()
    return converted


def process_job(identifier):
    directory = DATA / 'files' / identifier
    try:
        with STATE:
            item = get('job', identifier)
            if item.get('cancel'):
                return
            patch_job(identifier, state='Baixando', progress=0)
        check_space()
        if item.get('automatic'):
            item = prepare_library_job(item)
            if item is None:
                return
        elif item.get('resolve_format'):
            item = resolve_job_format(item)
        if get('job', identifier).get('cancel') or STOP.is_set():
            raise InterruptedError()
        directory.mkdir(parents=True, exist_ok=True)
        event('download_inicio', 'Obtenção de vídeo com áudio iniciada.' if item['output'] == 'mp4' else 'Obtenção do áudio iniciada.', identifier)
        media = get('media', item['media_id'])
        selected = next(f for f in media['formats'] if f['id'] == item['format_id'])
        if media['source'] == 'YouTube':
            result = run_engine({'action': 'download', 'url': media['url'], 'format_id': item['format_id'], 'directory': str(directory), 'video': selected.get('kind', 'audio') == 'video'}, identifier)
            source = directory / ('source.' + result['ext'])
        else:
            source = directory / ('source.' + selected['ext'])
            download_direct(media, source, identifier)
        source_data = probe(source, video=selected.get('kind', 'audio') == 'video')
        if get('job', identifier).get('cancel'):
            raise InterruptedError()
        try:
            image = cover_for('job', identifier)
        except Exception:
            image = None
        source = convert_media(source, directory, item, selected, image, float(source_data.get('format', {}).get('duration') or 0))
        ext = source.suffix[1:]
        if get('job', identifier).get('cancel'):
            raise InterruptedError()
        try:
            embedded = bool(image and covers.embed_existing(source, image, lambda path: probe(path, video=item['output'] == 'mp4')))
            patch_job(identifier, cover_embedded=embedded)
        except Exception:
            event('capa_indisponivel', 'Arquivo mantido sem alterar o áudio; a capa não pôde ser incluída.', identifier)
        if STOP.is_set() or get('job', identifier).get('cancel'):
            raise InterruptedError()
        destination = directory / filename(item['artist'], item['title'], ext)
        if source.stat().st_size > MAX_BYTES:
            raise SourceError('Arquivo final acima do limite de tamanho.')
        source.rename(destination)
        size = destination.stat().st_size
        saved_path = save_to_folder(destination, item['output_folder'], destination.name) if DESKTOP else None
        patch_job(identifier, state='Concluída', progress=100, file=destination.name,
                  size=size, saved_path=saved_path, expires=time.time() + TTL, error=None)
        if DESKTOP:
            shutil.rmtree(directory, ignore_errors=True)
            patch_job(identifier, file=None)
        event('arquivo_pronto', 'Arquivo validado e salvo na pasta escolhida.' if DESKTOP else 'Arquivo validado e pronto para salvar no aparelho.', identifier)
    except InterruptedError:
        resume = STOP.is_set() and not get('job', identifier).get('cancel')
        patch_job(identifier, state='Na fila' if resume else 'Cancelada', progress=0, file=None)
        shutil.rmtree(directory, ignore_errors=True)
        event('download_pausado' if resume else 'download_cancelado', 'Tarefa aguardando reabertura do Acervo.' if resume else 'Tarefa cancelada; temporários removidos.', identifier)
    except Exception as exc:
        message = str(exc) if isinstance(exc, SourceError) else 'Falha na obtenção ou validação do áudio. Analise o link e tente novamente.'
        patch_job(identifier, state='Erro', error=message, file=None)
        shutil.rmtree(directory, ignore_errors=True)
        event('download_erro', message, identifier)


def worker():
    while True:
        identifier = JOBS.get()
        try:
            if identifier is None:
                return
            if not STOP.is_set():
                process_job(identifier)
        finally:
            JOBS.task_done()


def cleanup_once():
    active_ids = set()
    for item in records('job'):
        if item['state'] in ACTIVE:
            active_ids.add(item['id'])
        elif item.get('expires') and item['expires'] < time.time() and item.get('file'):
            shutil.rmtree(DATA / 'files' / item['id'], ignore_errors=True)
            patch_job(item['id'], file=None, expired=True)
            event('limpeza', 'Arquivo temporário expirado e removido; histórico preservado.', item['id'])
    for folder in (DATA / 'files').iterdir():
        if folder.is_dir() and folder.name not in active_ids and time.time() - folder.stat().st_mtime > TTL:
            shutil.rmtree(folder, ignore_errors=True)
    with STATE:
        for token in list(SESSIONS):
            if SESSIONS[token] < time.time():
                del SESSIONS[token]
        DB.execute("DELETE FROM records WHERE kind='job' AND id NOT IN (SELECT id FROM records WHERE kind='job' ORDER BY updated DESC LIMIT ?) AND json_extract(body,'$.state') NOT IN ('Na fila','Baixando','Convertendo','Cancelando')", (5000 if DESKTOP else 500,))
        DB.commit()
    covers.cleanup(DATA / 'covers')


def cleaner():
    while not STOP.wait(300 if BACKGROUND.is_set() else 60):
        cleanup_once()


@asynccontextmanager
async def lifespan(_):
    global DB, PROXY_URL, PROXY, SPOTIFY, JOBS
    if len(PASSWORD) < 16:
        raise RuntimeError('Configure APP_PASSWORD com pelo menos 16 caracteres antes de iniciar.')
    if urlsplit(ORIGIN).scheme != 'https' and urlsplit(ORIGIN).hostname not in ('127.0.0.1', 'localhost'):
        raise RuntimeError('Hospedagem externa exige APP_ORIGIN com HTTPS.')
    for name in ('logs', 'files', 'covers'):
        (DATA / name).mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(DATA / 'logs' / 'acervo.jsonl', maxBytes=1048576, backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    DB = sqlite3.connect(DATA / 'acervo.sqlite3', check_same_thread=False)
    DB.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, body TEXT, updated REAL, PRIMARY KEY(kind,id))')
    DB.commit()
    if DESKTOP:
        from spotify_browser import SpotifyBrowser
        SPOTIFY = SpotifyBrowser(DATA / 'spotify-navegador', save_spotify_track, event)
    STOP.clear()
    JOBS = queue.Queue(maxsize=QUEUE_LIMIT)
    for item in records('job'):
        if item['state'] in ACTIVE:
            shutil.rmtree(DATA / 'files' / item['id'], ignore_errors=True)
            if DESKTOP and not item.get('cancel'):
                if item['state'] != 'Na fila' or item.get('progress') or item.get('bytes'):
                    patch_job(item['id'], state='Na fila', progress=0, bytes=0, file=None)
                JOBS.put_nowait(item['id'])
            else:
                patch_job(item['id'], state='Erro', error='Serviço reiniciado durante a tarefa. Tente novamente.', file=None)
    PROXY, PROXY_URL = start_proxy()
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(WORKERS)]
    for thread in threads:
        thread.start()
    cleanup_once()
    threading.Thread(target=cleaner, daemon=True).start()
    event('servico_inicio', f'Acervo iniciado: até {WORKERS} downloads por vez, com salvamento direto no PC.' if DESKTOP else f'Serviço iniciado: {WORKERS} tarefas simultâneas, arquivos temporários por {TTL // 3600} horas.')
    yield
    STOP.set()
    for _ in range(WORKERS):
        JOBS.put(None)
    if SPOTIFY:
        await __import__('asyncio').to_thread(SPOTIFY.close)
    for thread in threads:
        thread.join(timeout=25)
    cover_thread = globals().get('COVER_THREAD')
    if cover_thread:
        cover_thread.join(timeout=30)
    move_thread = globals().get('MOVE_THREAD')
    if move_thread:
        move_thread.join(timeout=30)
    PROXY.shutdown()
    PROXY.server_close()
    event('servico_fim', 'Acervo fechado.' if DESKTOP else 'Serviço encerrado.')
    DB.close()
    logger.removeHandler(handler)
    handler.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlsplit(ORIGIN).hostname])


def rate(key, limit):
    with STATE:
        ticks = RATES.setdefault(key, deque())
        while ticks and ticks[0] < time.monotonic() - 60:
            ticks.popleft()
        if len(ticks) >= limit:
            return False
        ticks.append(time.monotonic())
        return True


@app.middleware('http')
async def protect(request: Request, call_next):
    path = request.url.path
    if request.method not in ('GET', 'HEAD'):
        if request.headers.get('origin') != ORIGIN or request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'detail': 'Origem da solicitação inválida.'}, 403)
        length = request.headers.get('content-length', '')
        if not length.isdigit() or int(length) > 2 * 1024 * 1024:
            return JSONResponse({'detail': 'Envie um corpo de até 2 MB com tamanho declarado.'}, 413)
    if path.startswith('/api/') and path not in ('/api/login', '/api/health'):
        token = request.cookies.get('acervo_session', '')
        if SESSIONS.get(token, 0) < time.time():
            return JSONResponse({'detail': 'Entre com a senha do seu acervo.'}, 401)
    if path == '/api/login' and not rate('login', 5):
        return JSONResponse({'detail': 'Aguarde um minuto antes de tentar entrar novamente.'}, 429)
    if (path in ('/api/analyze', '/api/search', '/api/import', '/api/jobs', '/api/jobs/quick', '/api/library/download') or path.endswith('/retry')) and request.method == 'POST' and not rate('work', 30):
        return JSONResponse({'detail': 'Limite de 30 solicitações de trabalho por minuto.'}, 429)
    response = await call_next(request)
    response.headers.update({'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
                             'X-Frame-Options': 'DENY', 'Cache-Control': 'private, max-age=86400' if path.startswith('/api/covers/') and response.status_code == 200 else 'no-store',
                             'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"})
    return response


@app.exception_handler(SourceError)
async def source_error(_, exc):
    return JSONResponse({'detail': str(exc)}, 400)


class Login(BaseModel):
    password: str = Field(max_length=512)


@app.post('/api/login')
def login(body: Login):
    if not hmac.compare_digest(body.password.encode(), PASSWORD.encode()):
        raise HTTPException(401, 'Senha incorreta.')
    token = secrets.token_urlsafe(32)
    with STATE:
        if len(SESSIONS) >= 8:
            del SESSIONS[min(SESSIONS, key=SESSIONS.get)]
        SESSIONS[token] = time.time() + 43200
    response = JSONResponse({'ok': True})
    response.set_cookie('acervo_session', token, httponly=True, secure=ORIGIN.startswith('https:'), samesite='strict', max_age=43200)
    event('login', 'Acesso autenticado ao acervo pessoal.')
    return response


@app.post('/api/logout')
def logout(request: Request):
    with STATE:
        SESSIONS.pop(request.cookies.get('acervo_session'), None)
    response = JSONResponse({'ok': True})
    response.delete_cookie('acervo_session')
    return response


@app.get('/api/health')
def health():
    return {'ok': True, 'version': '1.9.0'}


def cover_for(kind, identifier):
    item = get(kind, identifier)
    fallback = item
    if kind in ('job', 'track') and item.get('media_id'):
        try:
            fallback = get('media', item['media_id'])
        except HTTPException:
            pass
    if kind == 'job' and item.get('item_id'):
        try:
            item = get('track', item['item_id'])
        except HTTPException:
            pass
    if not (item.get('thumbnail') or item.get('spotify_id') or str(item.get('id', '')).startswith('spotify-') or covers.thumbnail(fallback)):
        return None
    return covers.get_cover(DATA / 'covers', item, fallback)


@app.get('/api/covers/{kind}/{identifier}')
def cover_image(kind: str, identifier: str):
    if kind not in ('track', 'job', 'media'):
        raise HTTPException(404, 'Capa não encontrada.')
    image = cover_for(kind, identifier)
    if not image:
        raise HTTPException(404, 'Capa não disponível na fonte.')
    return FileResponse(image, media_type='image/jpeg')


def update_downloaded_covers():
    try:
        jobs = [j for j in records('job') if j['state'] == 'Concluída' and j.get('saved_path')
                and Path(j['saved_path']).suffix.lower() in ('.mp3', '.m4a', '.mp4', '.flac') and not j.get('cover_embedded')]
        COVER_TASK.update(total=len(jobs))
        for item in jobs:
            if STOP.is_set():
                break
            embedded = False
            try:
                image = cover_for('job', item['id'])
                if image and not STOP.is_set():
                    with FILES:
                        path = get('job', item['id'])['saved_path']
                        embedded = covers.embed_existing(path, image, lambda p: probe(p, video=item['output'] == 'mp4'))
                        if embedded:
                            patch_job(item['id'], cover_embedded=True, size=Path(path).stat().st_size)
            except Exception:
                pass
            COVER_TASK['done'] += 1
            COVER_TASK['embedded' if embedded else 'unavailable'] += 1
    finally:
        COVER_TASK['running'] = False
        event('capas_arquivos', f'Capas dos arquivos: {COVER_TASK["done"]}/{COVER_TASK["total"]} verificados, {COVER_TASK["embedded"]} incluídas, {COVER_TASK["unavailable"]} indisponíveis. Áudio preservado.')


@app.post('/api/covers/update-downloaded')
def update_covers():
    global COVER_THREAD
    if not DESKTOP:
        raise SourceError('Atualização de arquivos locais disponível no aplicativo Windows.')
    with STATE:
        if COVER_TASK['running']:
            return dict(COVER_TASK)
        COVER_TASK.update(running=True, done=0, total=0, embedded=0, unavailable=0)
        COVER_THREAD = threading.Thread(target=update_downloaded_covers, daemon=True)
        COVER_THREAD.start()
    return dict(COVER_TASK)


def move_download(identifier, folder):
    if os.name != 'nt':
        raise SourceError('Movimentação disponível no aplicativo Windows.')
    with FILES:
        item = get('job', identifier)
        if item['state'] != 'Concluída' or not item.get('saved_path'):
            raise SourceError('Somente arquivos concluídos podem ser movidos.')
        source = Path(item['saved_path'])
        if source.is_symlink():
            raise SourceError('O caminho virou um atalho. Escolha um arquivo gerenciado pelo Acervo.')
        source = source.resolve(strict=True)
        folder = Path(folder).resolve(strict=True)
        if not folder.is_dir():
            raise SourceError('Escolha uma pasta existente.')
        if source.parent == folder:
            return False
        before = source.stat()
        descriptor, name = tempfile.mkstemp(prefix='.acervo-mover-', suffix='.part', dir=folder)
        temporary, destination = Path(name), None
        try:
            checksum = hashlib.sha256()
            with os.fdopen(descriptor, 'wb') as output, source.open('rb') as incoming:
                while chunk := incoming.read(1024 * 1024):
                    if STOP.is_set() or MOVE_CANCEL.is_set():
                        raise InterruptedError()
                    checksum.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            after = source.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                raise SourceError('O arquivo foi alterado durante a cópia. Tente novamente.')
            with temporary.open('rb') as copied:
                if hashlib.file_digest(copied, 'sha256').digest() != checksum.digest() or temporary.stat().st_size != before.st_size:
                    raise SourceError('A cópia não passou na verificação. O original foi preservado.')
            if STOP.is_set() or MOVE_CANCEL.is_set():
                raise InterruptedError()
            for number in range(1, 10000):
                candidate = folder / (source.name if number == 1 else f'{source.stem} ({number}){source.suffix}')
                try:
                    # No Windows, rename na mesma unidade recusa substituir um arquivo existente.
                    temporary.rename(candidate)
                    destination = candidate
                    break
                except FileExistsError:
                    continue
            if destination is None:
                raise SourceError('Há muitos arquivos com esse nome na pasta escolhida.')
            with STATE:
                try:
                    patch_job(identifier, saved_path=str(destination), output_folder=str(folder))
                    source.unlink()
                except Exception:
                    save('job', item)
                    try:
                        destination.unlink()
                    except OSError:
                        patch_job(identifier, saved_path=str(destination), output_folder=str(folder))
                    raise SourceError('Não foi possível concluir a movimentação; os arquivos foram preservados. Feche outros players e tente novamente.')
            return True
        finally:
            temporary.unlink(missing_ok=True)


class MoveFiles(BaseModel):
    job_ids: list[str] = Field(min_length=1, max_length=5000)


def move_files(identifiers, folder):
    try:
        for identifier in identifiers:
            if STOP.is_set() or MOVE_CANCEL.is_set():
                MOVE_TASK['cancelled'] = True
                break
            try:
                moved = move_download(identifier, folder)
                MOVE_TASK['moved' if moved else 'skipped'] += 1
                if moved:
                    event('arquivo_movido', 'Arquivo movido com cópia verificada; histórico atualizado.', identifier)
            except InterruptedError:
                MOVE_TASK['cancelled'] = True
                break
            except Exception as exc:
                MOVE_TASK['errors'] += 1
                MOVE_TASK['last_error'] = str(exc) if isinstance(exc, SourceError) else 'Arquivo indisponível ou em uso. O original foi preservado.'
                event('mover_erro', MOVE_TASK['last_error'], identifier)
            MOVE_TASK['done'] += 1
    finally:
        MOVE_TASK['running'] = False
        event('mover_fim', f'Movimentação: {MOVE_TASK["moved"]} movido(s), {MOVE_TASK["skipped"]} já na pasta, {MOVE_TASK["errors"]} erro(s).' + (' Interrompida; arquivos não processados permanecem na origem.' if MOVE_TASK['cancelled'] else ''))


@app.post('/api/folders/move')
def start_move(body: MoveFiles):
    global MOVE_THREAD
    if not DESKTOP:
        raise SourceError('Movimentação disponível no aplicativo Windows.')
    with STATE:
        if MOVE_TASK['running']:
            raise SourceError('Há uma movimentação em andamento. Aguarde ou use Parar movimentação.')
        folder = config_read('move_folder')
        if not folder or not Path(folder).is_dir():
            raise SourceError('Escolha a pasta de destino na janela do Windows.')
        identifiers = list(dict.fromkeys(body.job_ids))
        for identifier in identifiers:
            item = get('job', identifier)
            if item['state'] != 'Concluída' or not item.get('saved_path'):
                raise SourceError('Selecione somente arquivos concluídos.')
        MOVE_CANCEL.clear()
        MOVE_TASK.update(running=True, done=0, total=len(identifiers), moved=0, skipped=0, errors=0, cancelled=False, last_error='')
        MOVE_THREAD = threading.Thread(target=move_files, args=(identifiers, folder), daemon=True)
        MOVE_THREAD.start()
    return dict(MOVE_TASK)


@app.post('/api/folders/stop')
def stop_move():
    MOVE_CANCEL.set()
    return {'ok': True}


@app.post('/api/folders/apply-pending')
def apply_folder_to_pending():
    if not DESKTOP:
        raise SourceError('Disponível no aplicativo Windows.')
    with STATE:
        folder = config_read('output_folder')
        if not folder or not Path(folder).is_dir():
            raise SourceError('Escolha a pasta na janela do Windows.')
        cursor = DB.execute("UPDATE records SET body=json_set(body,'$.output_folder',?,'$.updated',?),updated=? WHERE kind='job' AND json_extract(body,'$.state')='Na fila'", (folder, now(), time.time()))
        count = cursor.rowcount
        DB.commit()
    event('pasta_pendentes', f'Pasta atual aplicada a {count} tarefa(s) que ainda não começaram.')
    return {'count': count}


@app.get('/api/state')
def state(events_all: bool = False):
    jobs = records('job')
    for item in jobs:
        item['ready'] = bool(item.get('saved_path') and Path(item['saved_path']).is_file()) if DESKTOP else bool(item.get('file') and (DATA / 'files' / item['id'] / item['file']).is_file())
        item.pop('file', None)
    fields = ('id', 'media_id', 'item_id', 'title', 'artist', 'output', 'state', 'created', 'updated', 'progress', 'bytes', 'error', 'ready', 'expired', 'size', 'cover_embedded', 'video_from_audio', 'auto_selected')
    jobs = [{key: item[key] for key in fields if key in item} for item in jobs]
    tracks = records('track')
    for track in tracks:
        track['candidate_count'] = len(track.pop('candidates', []))
    return {'tracks': tracks, 'jobs': jobs if DESKTOP else jobs[:500], 'events': records('event')[:1000 if events_all else 100],
            'desktop': DESKTOP, 'output_folder': config_read('output_folder') if DESKTOP else None,
            'folder_mode': config_read('folder_mode') or 'same',
            'language': config_read('language') or 'en',
            'background_enabled': config_read('background_enabled') is not False,
            'desktop_runtime': dict(DESKTOP_RUNTIME),
            'spotify': SPOTIFY.status() if SPOTIFY else None,
            'cover_task': dict(COVER_TASK),
            'move_task': dict(MOVE_TASK),
            'engine': {'ffmpeg': bool(shutil.which('ffmpeg')), 'ffprobe': bool(shutil.which('ffprobe')), 'node': bool(shutil.which('node'))},
            'limits': {'file_mb': MAX_BYTES // 1048576, 'ttl_hours': TTL // 3600, 'workers': WORKERS, 'queue': QUEUE_LIMIT}}


class Settings(BaseModel):
    folder_mode: str = Field(pattern='^(ask|same)$')
    background_enabled: bool | None = None
    language: str | None = Field(default=None, pattern='^(en|pt-BR)$')


@app.post('/api/settings')
def settings(body: Settings):
    if not DESKTOP:
        raise SourceError('As preferências de pasta estão disponíveis no Windows.')
    config_write('folder_mode', body.folder_mode)
    if body.language is not None:
        config_write('language', body.language)
        event('idioma', 'Idioma da interface alterado: ' + body.language)
    if body.background_enabled is not None:
        config_write('background_enabled', body.background_enabled)
        event('configuracao_segundo_plano', 'Fechar/minimizar recolhe para a bandeja; downloads continuam.' if body.background_enabled else 'Fechar a janela encerra o programa e pausa tarefas pendentes.')
    event('configuracao', 'Destino: perguntar a cada download ou lote.' if body.folder_mode == 'ask' else 'Destino: usar sempre a pasta escolhida.')
    return {'ok': True}


class Link(BaseModel):
    url: str = Field(min_length=8, max_length=4096)


@app.post('/api/analyze')
def analyze_api(body: Link):
    if not NETWORK.acquire(blocking=False):
        raise HTTPException(429, 'Há análises em andamento. Aguarde.')
    try:
        results = analyze(body.url.strip())
        event('analise', f'Análise concluída: {len(results)} arquivo(s) compatível(is).')
        return {'results': results}
    except (OSError, ValueError) as exc:
        event('analise_erro', 'A fonte não pôde ser analisada.')
        if isinstance(exc, SourceError):
            raise
        raise SourceError('Falha ao acessar os metadados da fonte.')
    finally:
        NETWORK.release()


class Search(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    artist: str = Field(default='', max_length=200)
    source: str = Field(pattern='^(archive|youtube)$', default='archive')
    item_id: str | None = None


def lookup(track, source):
    if source == 'archive':
        results, warnings = archive_search(track['title'], track['artist'])
    else:
        results = run_engine({'action': 'search', 'query': track['title'] + ' ' + track['artist']})
        warnings = []
    results = [remember(m) for m in results]
    for media in results:
        media['match'] = match(track, media)
    results.sort(key=lambda m: m['match']['score'], reverse=True)
    return results, warnings


@app.post('/api/search')
def search(body: Search):
    if body.item_id and get('track', body.item_id)['state'] == 'Na fila':
        raise SourceError('Aguarde a tarefa desta música antes de pesquisar novamente.')
    if not NETWORK.acquire(blocking=False):
        raise HTTPException(429, 'Há buscas em andamento. Aguarde.')
    try:
        if body.item_id:
            track = get('track', body.item_id)
            results, warnings = lookup(track, body.source)
        elif body.source == 'archive':
            results, warnings = archive_search(body.title, body.artist)
        else:
            results = run_engine({'action': 'search', 'query': body.title + ' ' + body.artist})
            warnings = []
        results = [remember(m) for m in results]
        if body.item_id:
            track['candidates'] = results
            track['state'] = 'Encontrada' if results and results[0]['match']['confident'] else 'Precisa revisar' if results else 'Não encontrada'
            track['media_id'] = results[0]['id'] if track['state'] == 'Encontrada' else None
            save('track', track)
        event('busca', f'Busca em {"Internet Archive" if body.source == "archive" else "YouTube"}: {len(results)} candidato(s).')
        return {'results': results, 'warnings': warnings}
    except (OSError, ValueError) as exc:
        if body.item_id:
            track = get('track', body.item_id)
            track.update(state='Erro', error='Falha na busca externa. Tente novamente.')
            save('track', track)
        if isinstance(exc, SourceError):
            raise
        raise SourceError('A fonte de busca não respondeu. Tente novamente.')
    finally:
        NETWORK.release()


class Import(BaseModel):
    filename: str = Field(max_length=200)
    content: str = Field(max_length=1900000)


@app.post('/api/import')
def import_api(body: Import):
    if DESKTOP:
        raise SourceError('Use Conectar Spotify para carregar suas curtidas automaticamente.')
    if not body.filename.lower().endswith(('.csv', '.json')):
        raise SourceError('Escolha um arquivo CSV ou JSON.')
    tracks = parse_import(body.content, body.filename)
    existing = records('track')
    if len(existing) + len(tracks) > 500:
        raise SourceError('Limite de 500 músicas na biblioteca. Remova a lista anterior para importar outra.')
    count = 0
    for track in tracks:
        identifier = hashlib.sha256(json.dumps(track, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
        if any(t['id'] == identifier for t in existing):
            continue
        track.update(id=identifier, state='Precisa revisar', candidates=[], media_id=None)
        save('track', track)
        count += 1
    event('importacao', f'{count} música(s) importada(s). A busca deve ser iniciada pela interface.')
    return {'count': count}


class Selection(BaseModel):
    media_id: str = Field(max_length=32)


@app.get('/api/library/{identifier}')
def track_details(identifier: str):
    return get('track', identifier)


@app.post('/api/library/{identifier}/select')
def select(identifier: str, body: Selection):
    track = get('track', identifier)
    if track['state'] == 'Na fila':
        raise SourceError('Aguarde a tarefa desta música antes de trocar a gravação.')
    candidate = next((m for m in track.get('candidates', []) if m['id'] == body.media_id), None)
    if not candidate:
        raise SourceError('Escolha um candidato da busca desta música.')
    remember(candidate)
    track.update(media_id=body.media_id, state='Encontrada')
    save('track', track)
    event('revisao', 'Correspondência confirmada pelo usuário.')
    return {'ok': True}


@app.post('/api/library/clear')
def clear_library():
    if any(j['state'] in ACTIVE for j in records('job')):
        raise SourceError('Aguarde ou cancele as tarefas antes de remover a lista.')
    with STATE:
        DB.execute("DELETE FROM records WHERE kind='track'")
        DB.commit()
    event('biblioteca_limpa', 'Lista importada removida pelo usuário.')
    return {'ok': True}


class Download(BaseModel):
    media_id: str = Field(max_length=32)
    format_id: str = Field(max_length=50)
    output: str = Field(pattern='^(original|mp3|mp4)$', default='original')
    bitrate: int = Field(default=128)
    authorized: bool = False
    title: str = Field(min_length=1, max_length=200)
    artist: str = Field(min_length=1, max_length=200)
    item_id: str | None = None


def validate_format(media, format_id, output, bitrate):
    selected = next((f for f in media.get('formats', []) if f['id'] == format_id), None)
    if not selected:
        raise SourceError('Analise o link e escolha um formato disponível.')
    video = selected.get('kind', 'audio') == 'video'
    if output == 'original' and video:
        raise SourceError('O formato original usa somente áudio.')
    if output == 'mp3':
        abr = selected.get('abr')
        rates = [b for b in (32, 48, 64, 96, 128, 192) if b <= abr] if abr else [128]
        if bitrate != 0 and bitrate not in (rates or [32]):
            raise SourceError('Taxa MP3 incompatível com as opções da fonte.')
    if media.get('duration') and media['duration'] > 1200:
        raise SourceError('Limite de 20 minutos por arquivo.')
    if selected.get('size') and selected['size'] > MAX_BYTES:
        raise SourceError('Arquivo acima do limite de tamanho.')
    return selected


def resolve_job_format(item):
    media = get('media', item['media_id'])
    if not media.get('formats'):
        with NETWORK:
            media = analyze(media['url'])[0]
    formats = [f for f in media['formats'] if not f.get('size') or f['size'] <= MAX_BYTES]
    audio = [f for f in formats if f.get('kind', 'audio') == 'audio']
    video = [f for f in formats if f.get('kind') == 'video']
    choices = (video or audio) if item['output'] == 'mp4' else (audio or (video if item['output'] == 'mp3' else []))
    if not choices:
        raise SourceError('Nenhum formato compatível dentro dos limites. A conversão precisa de uma fonte acessível.')
    selected = max(choices, key=lambda f: (f.get('height') or f.get('abr') or 0, not f.get('transcode')))
    bitrate = 0 if item.get('automatic') or item.get('resolve_format') else item.get('bitrate', 0)
    validate_format(media, selected['id'], item['output'], bitrate)
    return patch_job(item['id'], media_id=media['id'], format_id=selected['id'], bitrate=bitrate, progress=0)


def prepare_library_job(item):
    track = get('track', item['item_id'])
    if item.get('media_id'):
        try:
            known = get('media', item['media_id'])
        except HTTPException:
            candidate = next((m for m in track.get('candidates', []) if m['id'] == item['media_id']), None)
            known = remember(candidate) if candidate else None
        if not known:
            item['media_id'] = None
    if not item.get('media_id'):
        patch_job(item['id'], progress=None)
        event('busca_fila', f'Pesquisando {track["artist"]} — {track["title"]}.', item['id'])
        candidates = track.get('candidates', []) if item.get('skip_review') else []
        expected_source = 'YouTube' if item['source_choice'] == 'youtube' else 'Internet Archive'
        if not candidates or any(m.get('source') != expected_source for m in candidates):
            with NETWORK:
                candidates, _ = lookup(track, item['source_choice'])
        if get('job', item['id']).get('cancel') or STOP.is_set():
            raise InterruptedError()
        chosen = candidates[0] if candidates and (candidates[0]['match']['confident'] or item.get('skip_review')) else None
        with STATE:
            track = get('track', item['item_id'])
            track.update(candidates=candidates, media_id=chosen['id'] if chosen else None)
            save('track', track)
        if not track['media_id']:
            status = 'Precisa revisar' if candidates else 'Não encontrada'
            message = 'Escolha a gravação na aba Revisar músicas.' if candidates else 'Nenhuma gravação compatível encontrada nesta fonte.'
            patch_job(item['id'], state=status, progress=0, error=message)
            event('revisao_pendente' if candidates else 'nao_encontrada', message, item['id'])
            return None
        remember(chosen)
        if item.get('skip_review'):
            patch_job(item['id'], auto_selected=True)
            event('escolha_sem_revisao', f'Escolha automática solicitada: {chosen["artist"]} — {chosen["title"]}. Confira o arquivo; a versão pode ser diferente.', item['id'])
        item['media_id'] = track['media_id']
    patch_job(item['id'], media_id=item['media_id'])
    return resolve_job_format(get('job', item['id']))


def enqueue(body):
    if not body.authorized:
        raise SourceError('Confirme que tem autorização para obter o arquivo.')
    if not shutil.which('ffprobe') or (body.output in ('mp3', 'mp4') and not shutil.which('ffmpeg')):
        raise SourceError('Instale FFmpeg e ffprobe no servidor antes de baixar.')
    media = get('media', body.media_id)
    validate_format(media, body.format_id, body.output, body.bitrate)
    if body.item_id:
        track = get('track', body.item_id)
        if any(j.get('item_id') == body.item_id and j['state'] in ACTIVE for j in records('job')):
            raise SourceError('Esta música já tem uma tarefa na fila.')
        if track.get('media_id') != body.media_id or track['state'] != 'Encontrada':
            raise SourceError('Revise e confirme a correspondência antes de baixar.')
    with STATE:
        if JOBS.full() or len([j for j in records('job') if j['state'] in ACTIVE]) >= QUEUE_LIMIT:
            raise HTTPException(429, f'A fila atingiu {QUEUE_LIMIT} tarefas. Aguarde.')
        check_space()
        item = body.model_dump()
        if DESKTOP:
            folder = config_read('output_folder')
            if not folder:
                raise SourceError('Escolha uma pasta para salvar seus áudios primeiro.')
            item['output_folder'] = folder
        item.update(id=secrets.token_hex(12), state='Na fila', created=now(), updated=now(), progress=0, bytes=0,
                    cancel=False, error=None, file=None)
        save('job', item)
        if body.item_id:
            track.update(job_id=item['id'])
            save('track', track)
        patch_job(item['id'], state='Na fila')
        JOBS.put_nowait(item['id'])
    event('fila', 'Tarefa adicionada à fila.', item['id'])
    return {'id': item['id']}


@app.post('/api/jobs')
def create_job(body: Download):
    return enqueue(body)


class QuickDownload(BaseModel):
    media_ids: list[str] = Field(min_length=1, max_length=500)
    output: str = Field(pattern='^(mp3|mp4)$')
    item_id: str | None = None
    authorized: bool = False


@app.post('/api/jobs/quick')
def quick_download(body: QuickDownload):
    if not DESKTOP or not body.authorized:
        raise SourceError('Use o aplicativo Windows e baixe somente arquivos autorizados.')
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise SourceError('Ferramentas incompletas. Extraia o pacote inteiro do Acervo.')
    with STATE:
        media = [get('media', identifier) for identifier in dict.fromkeys(body.media_ids)]
        track = get('track', body.item_id) if body.item_id else None
        if track and (len(media) != 1 or track.get('media_id') != media[0]['id'] or track['state'] not in ('Encontrada', 'Na fila', 'Concluída')):
            raise SourceError('Revise e confirme a gravação antes de baixar.')
        jobs = records('job')
        active = [j for j in jobs if j['state'] in ACTIVE]
        if track and any(j.get('item_id') == track['id'] and j['output'] == body.output for j in active):
            raise SourceError('Esta música já está na fila.')
        occupied = {(j.get('media_id'), j['output']) for j in active}
        pending = [m for m in media if (m['id'], body.output) not in occupied]
        if len(active) + len(pending) > QUEUE_LIMIT:
            raise SourceError('A seleção ultrapassa as vagas da fila. Aguarde os downloads.')
        folder = config_read('output_folder')
        if not folder:
            raise SourceError('Escolha a pasta de destino antes de baixar.')
        check_space()
        rows, identifiers = [], []
        for m in pending:
            item = dict(id=secrets.token_hex(12), media_id=m['id'], format_id=None, output=body.output,
                        bitrate=0, authorized=True, resolve_format=True, item_id=body.item_id,
                        title=(track or m)['title'][:200], artist=(track or m)['artist'][:200],
                        output_folder=folder, state='Na fila', created=now(), updated=now(),
                        progress=0, bytes=0, cancel=False, error=None, file=None)
            identifiers.append(item['id'])
            rows.append(('job', item['id'], json.dumps(item, ensure_ascii=False), time.time()))
            if track:
                track.update(job_id=item['id'], state='Na fila')
                rows.append(('track', track['id'], json.dumps(track, ensure_ascii=False), time.time()))
        DB.executemany('INSERT OR REPLACE INTO records VALUES (?,?,?,?)', rows)
        DB.commit()
        for identifier in identifiers:
            JOBS.put_nowait(identifier)
    event('fila_direta', f'{len(pending)} arquivo(s) na fila em {body.output.upper()}; {len(media)-len(pending)} já na fila.')
    return {'count': len(pending), 'skipped': len(media)-len(pending), 'job_ids': identifiers}


class LibraryBatch(BaseModel):
    track_ids: list[str] = Field(min_length=1, max_length=5000)
    source: str = Field(pattern='^(archive|youtube)$', default='youtube')
    output: str = Field(pattern='^(original|mp3|mp4)$', default='mp3')
    authorized: bool = False
    skip_review: bool = False


@app.post('/api/library/download')
def download_library(body: LibraryBatch):
    if not DESKTOP:
        raise SourceError('Downloads de toda a seleção estão disponíveis no aplicativo Windows.')
    if not body.authorized:
        raise SourceError('Confirme que tem autorização para obter os arquivos.')
    if not shutil.which('ffprobe') or not shutil.which('ffmpeg'):
        raise SourceError('Ferramentas incompletas. Extraia o pacote inteiro do Acervo.')
    with STATE:
        jobs = records('job')
        active = [j for j in jobs if j['state'] in ACTIVE]
        occupied = {j.get('item_id') for j in active if j['output'] == body.output}
        completed = {j.get('item_id') for j in jobs if j['state'] == 'Concluída' and j['output'] == body.output
                     and j.get('saved_path') and Path(j['saved_path']).is_file()}
        tracks = [get('track', identifier) for identifier in dict.fromkeys(body.track_ids)]
        pending = [t for t in tracks if t['id'] not in occupied | completed]
        if len(active) + len(pending) > QUEUE_LIMIT:
            raise SourceError(f'A seleção ultrapassa as {QUEUE_LIMIT} vagas da fila.')
        folder = config_read('output_folder')
        if not folder:
            raise SourceError('Escolha a pasta de destino antes de baixar.')
        check_space()
        rows, identifiers = [], []
        for track in pending:
            item = dict(id=secrets.token_hex(12), item_id=track['id'], title=track['title'][:200],
                        artist=track['artist'][:200], media_id=track.get('media_id') if track['state'] in ('Encontrada', 'Concluída', 'Na fila') else None,
                        format_id=None, output=body.output, bitrate=0, authorized=True, automatic=True,
                        skip_review=body.skip_review,
                        source_choice=body.source, output_folder=folder, state='Na fila', created=now(), updated=now(),
                        progress=0, bytes=0, cancel=False, error=None, file=None)
            track.update(state='Na fila', job_id=item['id'])
            identifiers.append(item['id'])
            for kind, record in (('job', item), ('track', track)):
                rows.append((kind, record['id'], json.dumps(record, ensure_ascii=False), time.time()))
        DB.executemany('INSERT OR REPLACE INTO records VALUES (?,?,?,?)', rows)
        DB.commit()
        for identifier in identifiers:
            JOBS.put_nowait(identifier)
    event('fila_lote', f'{len(identifiers)} arquivo(s) adicionado(s) em uma operação, formato {body.output.upper()}; {len(tracks)-len(pending)} já na fila ou salvos. Modo: {"sem revisão" if body.skip_review else "revisar correspondências duvidosas"}.')
    return {'count': len(identifiers), 'skipped': len(tracks)-len(pending), 'job_ids': identifiers}


@app.post('/api/jobs/cancel-all')
def cancel_all():
    count = 0
    with STATE:
        rows = []
        for item in records('job'):
            if item['state'] in ACTIVE:
                item.update(cancel=True, state='Cancelada' if item['state'] == 'Na fila' else 'Cancelando', updated=now())
                rows.append(('job', item['id'], json.dumps(item, ensure_ascii=False), time.time()))
                if item.get('item_id') and item['state'] == 'Cancelada':
                    try:
                        track = get('track', item['item_id'])
                        if track.get('job_id') == item['id']:
                            track['state'] = 'Precisa revisar'
                            rows.append(('track', track['id'], json.dumps(track, ensure_ascii=False), time.time()))
                    except HTTPException:
                        pass
                count += 1
        DB.executemany('INSERT OR REPLACE INTO records VALUES (?,?,?,?)', rows)
        DB.commit()
    event('fila_cancelada', f'{count} tarefa(s) cancelada(s) pelo usuário.')
    return {'count': count}


@app.post('/api/jobs/{identifier}/cancel')
def cancel(identifier: str):
    with STATE:
        item = get('job', identifier)
        if item['state'] in ACTIVE:
            patch_job(identifier, cancel=True, state='Cancelada' if item['state'] == 'Na fila' else 'Cancelando')
    return {'ok': True}


@app.post('/api/jobs/{identifier}/retry')
def retry(identifier: str):
    item = get('job', identifier)
    if item['state'] in ACTIVE:
        raise SourceError('Esta tarefa já está em andamento.')
    if item.get('automatic'):
        return download_library(LibraryBatch(track_ids=[item['item_id']], source=item['source_choice'], output=item['output'], authorized=item['authorized'], skip_review=item.get('skip_review', False)))
    if item.get('resolve_format'):
        if item.get('item_id'):
            track = get('track', item['item_id'])
            if any(j.get('item_id') == track['id'] and j['state'] in ACTIVE for j in records('job')):
                raise SourceError('Esta música já está na fila.')
            track.update(state='Encontrada', media_id=item['media_id']); save('track', track)
        return quick_download(QuickDownload(media_ids=[item['media_id']], output=item['output'], item_id=item.get('item_id'), authorized=item['authorized']))
    body = Download(**item)
    if body.item_id:
        try:
            track = get('track', body.item_id)
            if any(j.get('item_id') == body.item_id and j['state'] in ACTIVE for j in records('job')):
                raise SourceError('Esta música já tem uma tarefa na fila.')
            track.update(state='Encontrada', media_id=body.media_id)
            save('track', track)
        except HTTPException:
            body.item_id = None
    return enqueue(body)


@app.get('/api/jobs/{identifier}/file')
def file(identifier: str, play: bool = False):
    item = get('job', identifier)
    saved = Path(item['saved_path']) if DESKTOP and item.get('saved_path') else None
    if item['state'] != 'Concluída' or (not saved and (not item.get('file') or item.get('expires', 0) < time.time())):
        raise HTTPException(410, 'Arquivo indisponível ou expirado. Tente baixar novamente.')
    path = saved or DATA / 'files' / identifier / item['file']
    if not path.is_file():
        raise HTTPException(410, 'Arquivo temporário não encontrado.')
    event('reproducao' if DESKTOP else 'entrega', 'Arquivo salvo solicitado pelo player do Acervo.' if DESKTOP else 'Arquivo solicitado pelo navegador para salvar; gravação no aparelho não é confirmada pelo servidor.', identifier)
    return FileResponse(path, filename=path.name, media_type=(mimetypes.guess_type(path.name)[0] or 'audio/mpeg') if play else 'application/octet-stream', content_disposition_type='inline' if play else 'attachment')


@app.get('/desktop/boot/{ticket}')
def desktop_boot(ticket: str):
    global BOOT_TICKET
    if not DESKTOP or not BOOT_TICKET or not hmac.compare_digest(ticket, BOOT_TICKET):
        raise HTTPException(404)
    BOOT_TICKET = ''
    token = secrets.token_urlsafe(32)
    SESSIONS[token] = float('inf')
    response = RedirectResponse('/', status_code=303)
    response.set_cookie('acervo_session', token, httponly=True, samesite='strict')
    return response


def require_spotify():
    if not SPOTIFY:
        raise HTTPException(404)
    return SPOTIFY


@app.post('/api/spotify/connect')
def spotify_connect():
    require_spotify().begin()
    return {'ok': True}


@app.post('/api/spotify/sync')
def spotify_sync():
    require_spotify().start_sync()
    return {'ok': True}


@app.post('/api/spotify/disconnect')
def spotify_disconnect():
    require_spotify().disconnect()
    return {'ok': True}


@app.post('/api/spotify/stop')
def spotify_stop():
    require_spotify().stop_read()
    return {'ok': True}


@app.get('/')
def index():
    language = config_read('language') or 'en'
    payload = json.dumps({'language': language, 'catalog': CATALOG}, ensure_ascii=False).replace('<', '\\u003c')
    html = (BASE / 'static' / 'index.html').read_text('utf-8')
    html = html.replace('<!--APP_I18N-->', '<script type="application/json" id="app-i18n">' + payload + '</script>')
    html = html.replace('lang="pt-BR"', 'lang="' + language + '"')
    if DESKTOP:
        html = html.replace('<body>', '<body class="desktop">')
    return HTMLResponse(html, headers={'Content-Language': language})


app.mount('/static', StaticFiles(directory=BASE / 'static'), name='static')
