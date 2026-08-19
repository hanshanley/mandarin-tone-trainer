# Mandarin Tone Trainer

Mandarin Tone Trainer is an offline listening app for practicing the tones of
HSK 1–9 vocabulary. It plays a native recording while the word is hidden,
asks you to identify the tones, and then reveals the word, pinyin, definition,
and spoken-tone pattern.

It runs locally in a browser and can be packaged as a fully offline Android
app.

## Features

- 11,092 HSK vocabulary entries
- native recordings of complete words
- separate reference recordings for individual tones
- lexical and spoken-tone answers with sandhi support
- reference and human comparison voices
- local pronunciation recording and playback
- offline browser and Android use

## Quick start

You need Git, Python 3.10 or newer, FFmpeg, and about 1 GiB of free disk space.

On macOS with Homebrew:

```bash
brew install python ffmpeg
```

Clone, set up, and run the project:

```bash
git clone https://github.com/hanshanley/mandarin-tone-trainer.git
cd mandarin-tone-trainer
python3 scripts/bootstrap.py
python3 scripts/serve.py
```

Open <http://localhost:8000/app/>.

The Python bootstrap is the correct starting point on a fresh clone. It
installs a pinned local copy of Node.js and npm when needed, downloads the
audio collections, builds the offline assets, and validates the finished
setup. Downloads are resumable.

After setup, a new terminal can use `node`, `npm`, and `npx` normally. To
activate them immediately in the current zsh session:

```bash
source ~/.zprofile
```

To run the app again later:

```bash
cd mandarin-tone-trainer
python3 scripts/serve.py
```

## How practice works

1. Press **Native** to hear a complete word without seeing it.
2. Choose one tone for each syllable.
3. Review the word, pinyin, definition, and expected pattern.
4. Record yourself and compare your pronunciation with the native prompt.

Native playback always uses a recording of the complete word. Tone buttons use
independent, tone-specific syllable recordings. This prevents a full-word
recording from being reused as an inaccurate example of an isolated tone.

The **Comparison voice** setting switches the tone buttons between a clear
public-domain reference corpus and human `audio-cmn` syllables. Reviewed
fallbacks replace recordings that are duplicated, mislabeled, or unclear.

## Tone and sandhi support

The trainer distinguishes dictionary tones from tones heard in connected
speech. For example, 你好 has the lexical pattern `3-3`, but its usual spoken
realization is approximately `2-3`.

The data includes common third-tone sandhi, 不 and 一 changes, and neutral-tone
reductions. Recording-specific labels take priority when a speaker's actual
pronunciation differs from the default prediction.

## Android

Android builds require JDK 21, Android SDK Platform 36, Platform Tools, and
Build Tools 35.

After completing the quick start:

```bash
npm run android:debug
```

The debug APK is created at:

```text
android/app/build/outputs/apk/debug/app-debug.apk
```

See the [Android guide](docs/android.md) for release signing, installation,
and updates.

## Development

```bash
npm test                  # run the test suite
npm run verify:setup      # validate downloaded and generated assets
npm run build:mobile      # rebuild the offline web bundle
npm run android:debug     # build the Android debug APK
```

The main project directories are:

- `app/` — browser interface and playback logic
- `data/` — reviewed vocabulary, metadata, and audio-quality policy
- `scripts/` — setup, download, import, audit, and build tools
- `android/` — Capacitor Android project
- `tests/` — data, playback-policy, and bundle tests

Downloaded audio and generated assets are intentionally excluded from Git.
`python3 scripts/bootstrap.py` recreates them from pinned sources.

## Audio quality and maintenance

Known bad clips and approved fallbacks are recorded in
[`data/correction_audio_quality.json`](data/correction_audio_quality.json).
The audit checks file integrity, duplicate audio, pitch contours, runtime
selection, and bundle completeness. Ambiguous pitch results are left for
listening review instead of being rejected automatically.

See the [audio maintenance guide](docs/audio-maintenance.md) for corpus
updates, local imports, and individual audit tools.

## Sources and licenses

Definitions come from
[CC-CEDICT](https://www.mdbg.net/chinese/dictionary?page=cedict), maintained by
MDBG and distributed under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).

Native word and human syllable recordings come from `audio-cmn` under its
published CC BY-SA terms. Reference syllables come from
[`mp3-chinese-pinyin-sound`](https://github.com/davinfifield/mp3-chinese-pinyin-sound)
under the Unlicense.
