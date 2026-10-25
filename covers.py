"""Capas públicas, cache local limitado e metadados sem recodificar o áudio."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from security import SourceError, open_public

NETWORK = threading.BoundedSemaphore(2)
MAX_IMAGE = 2 * 1024 * 1024
MAX_CACHE = 128 * 1024 * 1024


def thumbnail(item):
    if item.get('thumbnail'):
        return item['thumbnail']
    p = urlsplit(item.get('url', ''))
    if p.hostname in ('www.youtube.com', 'youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be'):
        identifier = p.path.strip('/') if p.hostname == 'youtu.be' else parse_qs(p.query).get('v', [''])[0]
        if re.fullmatch(r'[A-Za-z0-9_-]{11}', identifier):
            return 'https://i.ytimg.com/vi/' + identifier + '/hqdefault.jpg'
    return None


def read_public(url, limit):
    connection, response = open_public(url, timeout=8)
    try:
        length = int(response.getheader('Content-Length', '0'))
        if length > limit:
            raise SourceError('Imagem ou metadados acima do limite.')
        data = response.read(limit + 1)
        if len(data) > limit:
            raise SourceError('Imagem ou metadados acima do limite.')
        return data
    finally:
        connection.close()


def get_cover(directory, item, fallback=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    spotify_id = item.get('spotify_id') or str(item.get('id', '')).removeprefix('spotify-')
    spotify_id = spotify_id if re.fullmatch(r'[A-Za-z0-9]{22}', spotify_id) else None
    image = thumbnail(item)
    alternative = thumbnail(fallback or {})
    key = hashlib.sha256((image or ('spotify:' + spotify_id if spotify_id else alternative or item.get('url') or item['id'])).encode()).hexdigest()
    target, missing = directory / (key + '.jpg'), directory / (key + '.missing')
    if target.is_file():
        return target
    if missing.exists() and time.time() - missing.stat().st_mtime < 3600:
        return None
    if not NETWORK.acquire(timeout=10):
        return None
    try:
        if target.is_file():
            return target
        if missing.exists() and time.time() - missing.stat().st_mtime < 3600:
            return None
        if not image and spotify_id:
            try:
                metadata = json.loads(read_public('https://open.spotify.com/oembed?' + urlencode({'url': 'https://open.spotify.com/track/' + spotify_id}), 65536))
                image = metadata.get('thumbnail_url')
            except (OSError, ValueError):
                pass
        for url in dict.fromkeys([image, alternative]):
            if not url:
                continue
            try:
                data = read_public(url, MAX_IMAGE)
                if not (data.startswith(b'\xff\xd8\xff') or data.startswith(b'\x89PNG\r\n\x1a\n') or (data.startswith(b'RIFF') and data[8:12] == b'WEBP')):
                    continue
                with tempfile.TemporaryDirectory(prefix='image-', dir=directory) as work:
                    source = Path(work) / 'source.image'
                    source.write_bytes(data)
                    probe = subprocess.run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_entries', 'stream=width,height', '-of', 'json', str(source)], capture_output=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                    dimensions = json.loads(probe.stdout)['streams'][0]
                    width, height = dimensions['width'], dimensions['height']
                    if not (0 < width <= 6000 and 0 < height <= 6000 and width * height <= 16000000):
                        continue
                    converted = Path(work) / 'cover.jpg'
                    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-i', str(source), '-vf', 'scale=480:480:force_original_aspect_ratio=decrease', '-frames:v', '1', '-threads', '1', '-q:v', '3', str(converted)], check=True, capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                    if converted.stat().st_size > MAX_IMAGE:
                        continue
                    os.replace(converted, target)
                    return target
            except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
                continue
        missing.touch()
        return None
    finally:
        NETWORK.release()


def embed_cover(path, cover):
    path = Path(path)
    data = Path(cover).read_bytes()
    if path.suffix.lower() == '.mp3':
        from mutagen.id3 import ID3, APIC, ID3NoHeaderError
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        if tags.getall('APIC'):
            return True
        tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Capa', data=data))
        tags.save(path, v2_version=3)
    elif path.suffix.lower() in ('.mp4', '.m4a'):
        from mutagen.mp4 import MP4, MP4Cover
        tags = MP4(path)
        if tags.get('covr'):
            return True
        tags['covr'] = [MP4Cover(data, imageformat=MP4Cover.FORMAT_JPEG)]
        tags.save()
    elif path.suffix.lower() == '.flac':
        from mutagen.flac import FLAC, Picture
        tags = FLAC(path)
        if tags.pictures:
            return True
        picture = Picture()
        picture.type, picture.mime, picture.desc, picture.data = 3, 'image/jpeg', 'Capa', data
        tags.add_picture(picture)
        tags.save()
    else:
        return False
    return True


def embed_existing(path, cover, validate):
    path = Path(path)
    handle, name = tempfile.mkstemp(prefix='.acervo-capa-', suffix=path.suffix, dir=path.parent)
    os.close(handle)
    temporary = Path(name)
    try:
        shutil.copy2(path, temporary)
        if not embed_cover(temporary, cover):
            return False
        validate(temporary)
        with temporary.open('r+b') as ready:
            os.fsync(ready.fileno())
        os.replace(temporary, path)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def cleanup(directory):
    files = sorted([*Path(directory).glob('*.jpg'), *Path(directory).glob('*.missing')], key=lambda p: p.stat().st_mtime)
    size = sum(p.stat().st_size for p in files if p.is_file())
    for path in files:
        if path.is_file() and (size > MAX_CACHE or time.time() - path.stat().st_mtime > 30 * 86400):
            size -= path.stat().st_size
            path.unlink(missing_ok=True)
