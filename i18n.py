"""Shared UI translations; stored music data and task states keep their values."""
import json
from pathlib import Path
import re

CATALOG = json.loads((Path(__file__).resolve().parent / 'static/locales/en.json').read_text(encoding='utf-8'))


def translate(text, language='en'):
    if language != 'en':
        return text
    value = ' '.join(str(text).split())
    if value in CATALOG['strings']:
        return CATALOG['strings'][value]
    for source, target in CATALOG['patterns'].items():
        expression = re.escape(source)
        expression = re.sub(r'\\\{\d+\\\}', '(.*?)', expression)
        match = re.fullmatch(expression, value) or re.fullmatch(expression, value + ' ')
        if match:
            return re.sub(r'\{(\d+)\}', lambda item: match.group(int(item[1]) + 1), target)
    sentences = re.split(r'(?<=[.!?])\s+', value)
    if len(sentences) > 1:
        translated = [translate(sentence, language) for sentence in sentences]
        if translated != sentences:
            return ' '.join(translated)
    return text
