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

## Recreate a complete setup after cloning

The repository commits the reviewed vocabulary, definitions, tone corrections,
application code, Android project, and exact upstream audio revisions. Large
generated audio and mobile assets are intentionally excluded from Git.

Prerequisites:

- Git
- Python 3.10 or newer
- FFmpeg on `PATH`
- About 1 GiB of free disk space

`requirements.txt` is intentionally empty of packages because the Python tools
use only the standard library. It is included so automated environments can
still run the conventional command safely:

```bash
python3 -m pip install -r requirements.txt
```

The Python bootstrap automatically downloads a pinned, project-local Node.js
22 toolchain when Node is missing or too old. No administrator access is
needed. If you prefer a system Node installation, the committed `.nvmrc`
selects the supported version:

```bash
nvm install
nvm use
```

On macOS with Homebrew:

```bash
brew install python node ffmpeg
```

On Ubuntu/Debian, install Python and FFmpeg with `apt`. On Windows, WSL is the
simplest supported environment for the browser setup; use Android Studio on
Windows for native Android builds.

From a clean clone, run the Python bootstrap. It installs Node dependencies,
downloads the pinned audio snapshots, recreates manifests and the offline
mobile bundle, and runs all validation:

```bash
git clone https://github.com/hanshanley/mandarin-tone-trainer.git
cd mandarin-tone-trainer
python3 scripts/bootstrap.py
```

`npm run setup` is an equivalent convenience alias.

The setup is resumable and automatically backs off/retries GitHub rate limits.
To reuse an existing `node_modules` directory:

```bash
python3 scripts/bootstrap.py --skip-npm-ci
```

To verify an existing setup without downloading:

```bash
npm run verify:setup
```

The pinned snapshots are declared in `config/source_snapshots.json`. The
bootstrap currently restores:

- 8,596 isolated audio-cmn word recordings
- 1,707 audio-cmn tone-specific syllable recordings
- 1,622 public-domain comparison recordings

It also verifies representative SHA-256 hashes, every manifest path, both
comparison-voice modes, the generated `www/` bundle, and the test suite.

### Run the browser app

```bash
python3 scripts/serve.py
```

Open `http://localhost:8000/app/`.

Native word prompts use the audio-cmn source. Tone-choice comparisons use the
public pinyin corpus by default and fall back to reviewed human audio when a
public clip is missing or quarantined.

The generated `audio/` and downloaded `imports/` directories are ignored by
Git and should not be committed. The generated `www/`,
`data/pinyin_public_recordings.json`, and `node_modules/` are also ignored and
recreated by the bootstrap.

The committed `data/hsk_words.json`, `data/definitions.json`,
`data/recordings.json`, and `data/correction_audio_quality.json` are canonical
reviewed inputs. Normal setup deliberately does **not** regenerate them from
moving external HSK or CC-CEDICT downloads.

### Troubleshooting setup

- `Missing required command`: install the named prerequisite and rerun setup.
- Interrupted audio download: rerun `npm run setup`; valid existing files are
  retained.
- `snapshot hash mismatch`: remove only the named generated audio file and
  rerun setup.
- Stale mobile files: run `npm run build:mobile`, then
  `npm run verify:setup`.
- Slow downloads: use more workers, for example
  `python3 scripts/bootstrap.py --workers 12`.

### Intentionally update an upstream audio snapshot

Do this only as a reviewed data change:

1. Change the 40-character revision in `config/source_snapshots.json`.
2. Download into a clean generated `audio/` and `imports/` setup.
3. Review corpus counts, hashes, duplicate checks, and native-speaker quality.
4. Update the expected counts/sample hashes in the snapshot file.
5. Run `npm run verify:setup`.

## Build and install the private Android app

The Android app is a Capacitor wrapper around the same browser app. It bundles
the generated data and every audio file reachable by the trainer, so practice
and recording work without a network connection. The current release APK is
about 154 MiB (161 MB).

Prerequisites:

- Node.js 22 or newer
- JDK 21
- Android SDK Platform 36, Platform Tools, and Build Tools 35
- The data and audio generated by the setup commands above

After `npm run setup`, build a debug APK:

```bash
npm run android:debug
```

The debug APK is written to
`android/app/build/outputs/apk/debug/app-debug.apk`.

### Configure private release signing

Create and securely back up a private keystore outside this repository:

```bash
mkdir -p ~/.local/share/mandarin-tone-trainer
keytool -genkeypair \
  -keystore ~/.local/share/mandarin-tone-trainer/release.jks \
  -alias mandarin-tone-trainer \
  -keyalg RSA \
  -keysize 4096 \
  -validity 10000
```

Copy `keystore.properties.example` to the ignored
`keystore.properties` file, then fill in the absolute keystore path, alias,
and passwords. Build the signed APK:

```bash
npm run android:release
```

The release artifact is
`android/app/build/outputs/apk/release/app-release.apk`. Never commit the
keystore, `keystore.properties`, or their passwords. Back up the keystore and
credentials together: Android will reject an in-place update signed with a
different key.

### Sideload on a Pixel

For installation over USB, enable Developer options and USB debugging on the
phone, connect it, approve the computer, and run:

```bash
adb install android/app/build/outputs/apk/release/app-release.apk
```

For later builds, increment `versionCode` in `android/app/build.gradle`, rebuild
with the same keystore, and update in place:

```bash
adb install -r android/app/build/outputs/apk/release/app-release.apk
```

Without USB debugging, transfer the release APK to the phone, open it from the
Files app, and allow **Install unknown apps** for Files when Android prompts.
The first use of **Record me** requests microphone permission. The rest of the
trainer remains usable if that permission is denied.

If `resources/logo.svg` changes, regenerate the committed launcher and splash
resources with Android Studio's Image Asset tools. Android build output,
copied web assets, downloaded audio, local SDK settings, and signing material
are all excluded from Git.

Definitions are sourced from the
[official CC-CEDICT download](https://www.mdbg.net/chinese/dictionary?page=cedict)
maintained by MDBG. Those definition fields are distributed under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).

Correction buttons prefer the public-domain
[`mp3-chinese-pinyin-sound`](https://github.com/davinfifield/mp3-chinese-pinyin-sound)
corpus and fall back to human audio-cmn syllables when a public clip is
unavailable or quarantined. Known defects are recorded in
`data/correction_audio_quality.json`; items with no good source are excluded
rather than playing an incorrect sound. Correction playback normalizes active
speech loudness and uses a peak limiter; public clips also receive a gentle
presence/high-shelf EQ with headroom, without changing pitch or timing.
The **Comparison voice** setting switches every tone button between the clear
reference corpus and human recordings for the current practice session.

Native word prompts always use the real human audio-cmn word recording.
To avoid grading the wrong reading, metadata-free recordings are excluded when
the same Hanzi has multiple pronunciations, and prosodically ambiguous tone
groups require a recording-specific surface label before entering the quiz.

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

The importer preserves these recordings in `data/recordings.json`, but the
current trainer intentionally selects only verified `audio_cmn` word prompts.
Support for choosing other indexed sources is future work.

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
python3 scripts/add_definitions.py
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

The browser adds 120 ms of leading silence and 200 ms of trailing silence to
the decoded correction audio in memory. It copies the decoded PCM unchanged,
so pitch, timing, and consonant distinctions are preserved without another
lossy encode. For exported padded MP3 copies, use:

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
speaker recordings. They are indexed for future source-selection support but
are not selected by the current trainer.

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
