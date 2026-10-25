import csv
import difflib
import io
import json
import re
import unicodedata
from pathlib import PurePosixPath
from urllib.parse import quote, urlencode, unquote

from security import SourceError, open_public

AUDIO_EXT = {'mp3', 'm4a', 'aac', 'ogg', 'opus', 'flac', 'wav', 'webm'}


def clean(value):
    if isinstance(value, list):
        return ', '.join(clean(x) for x in value)
    if isinstance(value, dict):
        return str(value.get('name', ''))
    return str(value or '').strip()[:300]


def norm(value):
    return ' '.join(re.findall(r'\w+', ''.join(c for c in unicodedata.normalize('NFKD', clean(value).lower()) if not unicodedata.combining(c))))


def seconds(value, milliseconds=False):
    try:
        if ':' in str(value):
            total = 0
            for part in str(value).split(':'):
                total = total * 60 + float(part)
            return total
        result = float(value) / (1000 if milliseconds else 1)
        return result if 0 < result < 86400 else None
    except (TypeError, ValueError):
        return None


def parse_import(content, filename):
    try:
        if filename.lower().endswith('.json'):
            rows = json.loads(content.lstrip('\ufeff'))
            if isinstance(rows, dict):
                rows = rows.get('tracks', rows.get('items', rows.get('musicas')))
        else:
            sample = content.lstrip('\ufeff')
            try:
                dialect = csv.Sniffer().sniff(sample[:4096], delimiters=',;\t')
            except csv.Error:
                dialect = csv.excel
            rows = list(csv.DictReader(io.StringIO(sample), dialect=dialect))
        if not isinstance(rows, list) or len(rows) > 500:
            raise SourceError('Importe uma lista com até 500 músicas.')
        tracks, seen = [], set()
        for index, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                raise SourceError(f'Linha {index}: registro inválido.')
            row = row.get('track', row)
            if not isinstance(row, dict):
                raise SourceError(f'Linha {index}: música inválida.')
            row = {norm(k): v for k, v in row.items()}
            def field(*names):
                return next((row[norm(n)] for n in names if norm(n) in row), None)
            title = clean(field('title', 'titulo', 'música', 'track name', 'name'))
            artist = clean(field('artist', 'artista', 'artists', 'artist name(s)', 'artist name'))
            if not title or not artist:
                raise SourceError(f'Linha {index}: título e artista são obrigatórios. Veja os exemplos.')
            album = clean(field('album', 'album name'))
            duration_ms = field('duration_ms', 'duration ms', 'duration (ms)')
            duration = seconds(duration_ms, True) if duration_ms is not None else seconds(field('duration', 'duracao', 'duration seconds'))
            key = (norm(title), norm(artist), norm(album))
            if key not in seen:
                tracks.append({'title': title, 'artist': artist, 'album': album, 'duration': duration})
                seen.add(key)
        if not tracks:
            raise SourceError('A lista não contém músicas.')
        return tracks
    except (json.JSONDecodeError, csv.Error, AttributeError):
        raise SourceError('CSV ou JSON inválido. Veja os arquivos de exemplo.')


VERSIONS = {'live', 'ao vivo', 'cover', 'remix', 'remaster', 'acoustic', 'acustico', 'instrumental', 'karaoke', 'sped up', 'slowed', 'edit', 'demo', 'tribute'}


def match(track, candidate):
    t, c = norm(track['title']), norm(candidate['title'])
    a, b = norm(track['artist']), norm(candidate.get('artist'))
    if candidate.get('source') == 'YouTube':
        artist = re.sub(r'\s*-\s*Topic$|VEVO$', '', clean(candidate.get('artist')), flags=re.I).strip()
        b = norm(artist)
        title = re.sub(r'[\[(]\s*(?:official(?: music)? (?:audio|video)|audio|lyrics?|lyric video|visuali[sz]er|áudio oficial|vídeo oficial)\s*[\])]', '', clean(candidate['title']), flags=re.I).strip()
        parts = re.split(r'\s+[-–—:]\s+', title, maxsplit=1)
        if len(parts) == 2 and norm(parts[0]) in (a, b):
            title = parts[1]
        c = norm(title)
    reasons = []
    score = 70 * difflib.SequenceMatcher(None, t, c).ratio() + 30 * difflib.SequenceMatcher(None, a, b).ratio()
    def versions(text):
        return {v for v in VERSIONS if re.search(r'\b' + v + (r'(?:ed)?' if v == 'remaster' else '') + r'\b', text)}
    if versions(t) != versions(c):
        score -= 40
        reasons.append('Versão diferente: confira cover, remix ou ao vivo.')
    td, cd = track.get('duration'), candidate.get('duration')
    if td and cd and abs(td - cd) > max(5, td * .03):
        score -= 30
        reasons.append('Duração diferente da lista importada.')
    if track.get('album') and candidate.get('album') and norm(track['album']) != norm(candidate['album']):
        score -= 20
        reasons.append('Álbum diferente.')
    if not td or not cd:
        reasons.append('Duração não confirmada.')
    confident = score >= 96 and t == c and a == b and bool(td and cd) and not reasons
    return {'score': max(0, round(score)), 'confident': confident,
            'reason': ('Título, artista e duração compatíveis.' + (' Álbum não informado pela fonte.' if track.get('album') and not candidate.get('album') else '')) if confident else ' '.join(reasons or ['Confira a gravação antes de selecionar.'])}


def read_json(url):
    conn, response = open_public(url)
    try:
        data = response.read(4 * 1024 * 1024 + 1)
        if len(data) > 4 * 1024 * 1024:
            raise SourceError('Metadados da fonte acima do limite.')
        return json.loads(data)
    finally:
        conn.close()


def archive_item(identifier):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,200}', identifier):
        raise SourceError('Identificador do Internet Archive inválido.')
    data = read_json('https://archive.org/metadata/' + identifier)
    meta = data.get('metadata', {}) if isinstance(data, dict) else {}
    if not isinstance(data, dict) or data.get('is_dark') or str(meta.get('access-restricted-item', '')).lower() == 'true':
        raise SourceError('Item restrito: não é possível obter arquivos públicos.')
    results = []
    for f in data.get('files', []):
        name = f.get('name', '')
        ext = PurePosixPath(name).suffix.lower().lstrip('.')
        if ext not in AUDIO_EXT or f.get('private') or f.get('viruscheck') == 'failed':
            continue
        results.append({'url': 'https://archive.org/download/' + identifier + '/' + quote(name, safe='/'),
                        'title': clean(f.get('title') or PurePosixPath(name).stem),
                        'artist': clean(f.get('artist') or meta.get('creator')) or 'Artista não informado',
                        'album': clean(f.get('album') or meta.get('title')),
                        'duration': seconds(f.get('length')), 'source': 'Internet Archive',
                        'thumbnail': 'https://archive.org/services/img/' + identifier,
                        'license': clean(meta.get('licenseurl')),
                        'formats': [{'id': 'original', 'ext': ext, 'abr': seconds(f.get('bitrate')),
                                     'size': int(f['size']) if str(f.get('size', '')).isdigit() else None}]})
        if len(results) == 60:
            break
    return results


def archive_search(title, artist):
    terms = re.findall(r'\w+', title + ' ' + artist, flags=re.UNICODE)[:20]
    query = 'mediatype:audio AND ' + ' AND '.join('"' + t + '"' for t in terms)
    data = read_json('https://archive.org/advancedsearch.php?' + urlencode({'q': query, 'output': 'json', 'rows': 4, 'fl[]': 'identifier'}))
    results, warnings = [], []
    for item in data.get('response', {}).get('docs', []):
        try:
            results.extend(archive_item(item['identifier'])[:15])
        except (SourceError, OSError, ValueError):
            warnings.append('Um item do Internet Archive não respondeu.')
    query_track = {'title': title, 'artist': artist}
    results.sort(key=lambda item: match(query_track, item)['score'], reverse=True)
    return [item for item in results if match(query_track, item)['score'] >= 40][:40], warnings
