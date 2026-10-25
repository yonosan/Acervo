"""Isolated yt-dlp worker. One JSON request on stdin, JSON events on stdout."""
import json
import sys
import time
from pathlib import Path


def audio_formats(info):
    return [f for f in info.get('formats', []) if f.get('vcodec') == 'none'
            and f.get('acodec') not in ('none', None) and not f.get('has_drm')
            and f.get('protocol') == 'https' and f.get('ext') in ('m4a', 'webm', 'opus', 'mp3', 'ogg')]


def available_formats(info):
    formats = [{'id': f['format_id'], 'ext': f['ext'], 'abr': f.get('abr'), 'kind': 'audio',
                'size': f.get('filesize') or f.get('filesize_approx')} for f in audio_formats(info)]
    audio = [f for f in audio_formats(info) if f.get('ext') == 'm4a' and str(f.get('acodec', '')).startswith('mp4a')]
    best_audio = max(audio, key=lambda f: f.get('abr') or 0, default=None)
    for video in info.get('formats', []):
        if (video.get('has_drm') or video.get('protocol') != 'https' or video.get('ext') not in ('mp4', 'webm')
                or video.get('vcodec') in ('none', None)
                or not 0 < (video.get('height') or 0) <= 1080):
            continue
        compatible = video.get('ext') == 'mp4' and str(video.get('vcodec', '')).startswith(('avc1', 'h264'))
        parts = [video]
        if video.get('acodec') == 'none':
            companion = best_audio if compatible else max(audio_formats(info), key=lambda f: f.get('abr') or 0, default=None)
            if not companion:
                continue
            parts.append(companion)
        compatible = compatible and str(parts[-1].get('acodec', '')).startswith('mp4a')
        sizes = [f.get('filesize') or f.get('filesize_approx') for f in parts]
        formats.append({'id': '+'.join(f['format_id'] for f in parts), 'ext': 'mp4' if compatible else 'mkv' if len(parts) > 1 else video['ext'], 'kind': 'video',
                        'height': video['height'], 'transcode': not compatible,
                        'size': sum(sizes) if all(sizes) else None, 'abr': None})
    return formats


def brief(info):
    return {'url': info.get('webpage_url') or f'https://www.youtube.com/watch?v={info["id"]}',
            'title': info.get('track') or info.get('title') or 'Sem título',
            'artist': info.get('artist') or info.get('uploader') or 'Artista não informado',
            'album': info.get('album'), 'duration': info.get('duration'), 'source': 'YouTube',
            'thumbnail': info.get('thumbnail'),
            'formats': available_formats(info)}


def playlist_entries(info, limit=500):
    import re
    entries, seen = [], set()
    for entry in info.get('entries') or []:
        if not entry or not re.fullmatch(r'[A-Za-z0-9_-]{11}', str(entry.get('id', ''))):
            continue
        if entry.get('availability') in ('private', 'premium_only', 'subscriber_only', 'needs_auth') or entry.get('is_live'):
            continue
        if entry['id'] in seen:
            continue
        seen.add(entry['id'])
        entries.append(brief({**entry, 'webpage_url': 'https://www.youtube.com/watch?v=' + entry['id']}))
        if len(entries) > limit:
            raise ValueError(f'A playlist ultrapassa o limite de {limit} vídeos por análise.')
    if not entries:
        raise ValueError('Nenhum vídeo público disponível nesta playlist.')
    return entries


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


class Quiet:
    def debug(self, *_):
        pass
    def warning(self, *_):
        pass
    def error(self, *_):
        pass


def main():
    import yt_dlp
    req = json.loads(sys.stdin.readline())
    last = [0.0]
    received, total_size = {}, [None]

    def progress(d):
        received[d.get('filename', 'source')] = d.get('downloaded_bytes', 0)
        if time.monotonic() - last[0] > .5:
            downloaded = sum(received.values())
            emit({'progress': min(94, downloaded / total_size[0] * 94) if total_size[0] else None,
                  'bytes': downloaded})
            last[0] = time.monotonic()

    options = {'proxy': req['proxy'], 'quiet': True, 'no_warnings': True, 'logger': Quiet(),
               'noplaylist': True, 'socket_timeout': 15, 'retries': 1, 'fragment_retries': 1,
               'cachedir': False, 'js_runtimes': {'node': {}}, 'remote_components': set(),
               'allowed_extractors': ['youtube.*'], 'max_filesize': req.get('max_bytes', 104857600),
               'progress_hooks': [progress], 'concurrent_fragment_downloads': 1,
               'hls_prefer_native': True}
    if req['action'] == 'search':
        options['extract_flat'] = True
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info('ytsearch5:' + req['query'], download=False)
            emit({'result': [brief(e) for e in info.get('entries', []) if e]})
        return
    if req['action'] == 'playlist':
        options.update(noplaylist=False, extract_flat='in_playlist', playlistend=501, ignoreerrors=True)
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(req['url'], download=False)
            if not info or info.get('_type') != 'playlist':
                raise ValueError('O endereço não contém uma playlist pública do YouTube.')
            emit({'result': playlist_entries(info)})
        return
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(req['url'], download=False)
        if info.get('_type') in ('playlist', 'multi_video') or info.get('is_live'):
            raise ValueError('Use o link de um vídeo gravado individual.')
        if info.get('duration', 0) > req.get('max_duration', 1200):
            raise ValueError('O áudio ultrapassa o limite de duração.')
        formats = available_formats(info)
        if not formats:
            raise ValueError('Nenhum fluxo HTTPS de áudio ou vídeo disponível sem restrições.')
        if req['action'] == 'analyze':
            emit({'result': brief(info)})
            return
        selected = next((f for f in formats if f['id'] == req['format_id']), None)
        if not selected:
            raise ValueError('O formato mudou na origem. Analise o link novamente.')
        if (selected.get('size') or 0) > req['max_bytes']:
            raise ValueError('Arquivo acima do limite.')
    total_size[0] = selected.get('size')
    options.update({'format': req['format_id'], 'outtmpl': str(Path(req['directory']) / 'source.%(ext)s'),
                    'merge_output_format': selected['ext'] if selected['kind'] == 'video' else None})
    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([req['url']])
    emit({'result': {'ext': selected['ext']}})


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Raw extractor messages can contain signed URLs. Do not persist them.
        emit({'error': str(exc) if isinstance(exc, ValueError) else
              'YouTube indisponível: bloqueio da origem, formato ausente ou motor desatualizado. Não serão usados cookies ou desvios de acesso.'})
        sys.exit(1)
