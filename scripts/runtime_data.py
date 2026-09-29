"""Legacy acoustic-audit inputs; publisher drills use their separate source index."""
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
    imported = json.loads((ROOT / 'data/mandarin_native_recordings.json').read_text(encoding='utf-8'))['recordings']
    recordings += [recording for recording in imported if recording['recording_type'] == 'word_candidate']
    recordings += json.loads((ROOT / 'data/sinosplice_recordings.json').read_text(encoding='utf-8'))['recordings']
    return recordings
