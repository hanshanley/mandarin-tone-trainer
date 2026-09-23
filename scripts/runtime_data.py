"""Canonical vocabulary and all indexed recording sources used by practice."""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_words():
    words = json.loads((ROOT / 'data/hsk_words.json').read_text(encoding='utf-8'))
    words += json.loads((ROOT / 'data/mandarin_native_words.json').read_text(encoding='utf-8'))['words']
    identifiers = [word['id'] for word in words]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError('Duplicate runtime vocabulary IDs')
    return words


def read_recordings():
    recordings = json.loads((ROOT / 'data/recordings.json').read_text(encoding='utf-8'))
    for filename in ('mandarin_native_recordings.json', 'context_word_recordings.json'):
        recordings += json.loads((ROOT / 'data' / filename).read_text(encoding='utf-8'))['recordings']
    return recordings
