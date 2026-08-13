# Mandarin Tone Trainer — HSK 1–9, multi-speaker, sandhi-aware

A local browser app for practicing Mandarin tones using **many human speakers per word**. The HSK vocabulary layer contains 11,092 entries. Audio is modeled as `word × speaker × recording`, so a word such as 公司 can have every available Mandarin recording rather than one canonical voice.

## Tone model

Every HSK item stores:

- `lexical_pattern`: dictionary tones (e.g. 你好 = `3-3`)
- `default_surface_pattern`: expected heard realization (e.g. 你好 ≈ `2-3`)
- `sandhi_tags`: third-tone sandhi, 不, 一, neutral tone, etc.
- `surface_label_needs_clip_review`: true for cases where word-level rules are insufficient (especially longer T3 strings / prosodic grouping)

Every **recording** can additionally store its own `surface_pattern`. That clip-level label wins in the listening quiz. This is important because the goal is to practice what a particular native speaker actually says, not just what a dictionary predicts.

Neutral tone is represented as `N`.

## Set up and run

Downloaded audio is intentionally excluded from Git. After cloning the
repository, download the complete audio-cmn word and syllable collections,
build the local index, and start the server:

```bash
cd mandarin-tone-trainer

python3 scripts/download_audio_cmn.py --all-source
python3 scripts/download_audio_cmn_syllables.py
python3 scripts/import_local_audio.py
python3 scripts/add_pinyin_syllables.py

python3 scripts/serve.py
```

Then open `http://localhost:8000/app/`. The app selects `audio-cmn` by default
when that source is present. The commands download 8,596 word recordings and
1,707 tone-specific syllable clips. They are resumable, so running them again
keeps valid existing files.

The generated `audio/` and downloaded `imports/` directories are ignored by
Git and should not be committed.

## Add local human audio

Put audio in folders by source and exact Mandarin word:

```text
imports/
  forvo/
    公司/
      rhapsodia.mp3
      zizi.mp3
      shadow0ing.mp3
```

Optional sidecar metadata:

```json
{
  "speaker": "rhapsodia",
  "sex": "f",
  "country": "CHN",
  "region": "Beijing",
  "rate": 4,
  "surface_pattern": "1-1",
  "source_url": "https://forvo.com/word/公司/#zh"
}
```

Then:

```bash
python3 scripts/import_local_audio.py
```

Reload the app. It randomizes among all indexed recordings for the chosen word.

## Bootstrap direct HSK audio with audio-cmn

The project includes a resumable downloader for the open human Mandarin
`audio-cmn` corpus. It matches the local HSK vocabulary, downloads every
matching isolated-word MP3, and writes Mandarin/language/license provenance
sidecars:

```bash
python3 scripts/download_audio_cmn.py --all-source
python3 scripts/download_audio_cmn_syllables.py
python3 scripts/import_local_audio.py
python3 scripts/add_pinyin_syllables.py
```

Use `--quality 64k` for smaller files, or `--limit 20` to smoke-test first.
Use `--all-source` to enumerate and download all 8,596 files published by
audio-cmn, including source vocabulary not present in the local HSK 3.0 list.
The source is CC-BY-SA and covers the older HSK 2000 vocabulary, so missing
words in the HSK 3.0 list are expected. The importer preserves each source
recording separately and marks it as verified Mandarin (`language_code: zh`).
To check the downloaded syllable clips for suspicious internal gaps:

```bash
python3 scripts/check_audio_cmn_syllables.py
```

Correction playback always uses the tone-specific `syllabs` recordings, never
an unlabeled word recording. Words with multiple HSK readings are excluded
unless a recording has an explicit matching `surface_pattern`, preventing one
Hanzi spelling from being paired with the wrong reading.

To add playback padding without modifying or repeatedly re-encoding the source
clips, write to the separate default output directory:

```bash
python3 scripts/pad_audio_cmn_syllables.py
```

## Small OpenAI TTS sample

OpenAI-generated voices are kept separate from human recordings. To create a
small local sample for 公司, 你好, and 谢谢:

```bash
export OPENAI_API_KEY='...'
python3 scripts/download_openai_tts_sample.py
python3 scripts/import_local_audio.py
```

The default sample uses `coral`, `marin`, and `cedar` with
`gpt-4o-mini-tts`. These files are synthetic Mandarin examples, not native
speaker recordings.

## Forvo: inventory every Mandarin pronunciation

Forvo's official `word-pronunciations` API returns all pronunciations for a word and can be restricted to `language=zh`. The included script inventories every Mandarin speaker returned:

```bash
export FORVO_API_KEY='...'
python3 scripts/forvo_inventory.py
```

It **does not cache/download API audio** because Forvo's current API terms say audio links expire after two hours and API pronunciations may not be cached. Locally obtained recordings can still be indexed with `import_local_audio.py`.

The free/individual API plan is currently limited to 500 requests/day, so the script supports resumable chunks:

```bash
python3 scripts/forvo_inventory.py --start 0 --limit-words 500
python3 scripts/forvo_inventory.py --start 500 --limit-words 500
```

## Other Mandarin speakers

`scripts/common_voice_index.py` can index HSK words occurring inside a downloaded Mandarin Common Voice corpus. Those are marked as **context** clips rather than pretending the entire sentence is an isolated-word recording. A later forced-alignment pass can crop exact word spans.

This distinction gives the trainer two useful difficulty levels:

1. isolated native word recordings (Forvo / word corpora)
2. natural contextual Mandarin from many speakers

## Sandhi included, not filtered

The app intentionally keeps sandhi-sensitive items. Surface mode trains the realized tones; lexical mode trains the dictionary pattern; mixed mode alternates the two. Basic rules implemented in the preprocessing layer include:

- `3 + 3 → 2 + 3`
- 不: `4 → 2` before a fourth tone
- 一: `1 → 2` before T4, and `1 → 4` before T1/T2/T3 in ordinary connected use
- common `A不A` / reduplicative 一 reductions to neutral tone
- lexical neutral-tone syllables

Longer third-tone sequences are retained and flagged for clip-level review because their realization depends on prosodic grouping.
