# Mandarin Tone Trainer

An offline Mandarin listening trainer for HSK 1–9 vocabulary. It runs in a
browser or as an Android app and uses native word recordings, tone-specific
comparison audio, and sandhi-aware answers.

## Run the app

### Requirements

- Git
- Python 3.10 or newer
- FFmpeg
- about 1 GiB of free disk space

On macOS with Homebrew:

```bash
brew install python ffmpeg
```

### First-time setup

Run these commands in order:

```bash
git clone https://github.com/hanshanley/mandarin-tone-trainer.git
cd mandarin-tone-trainer
python3 scripts/bootstrap.py
python3 scripts/serve.py
```

Then open <http://localhost:8000/app/>.

> **Start with the Python bootstrap, not npm.** The bootstrap installs a
> pinned project-local version of Node.js and npm when they are unavailable.

The first setup downloads approximately 180 MiB of audio and may take several
minutes. It is resumable, so rerunning the same command is safe.

### Run it again later

From the project directory:

```bash
python3 scripts/serve.py
```

Open <http://localhost:8000/app/>.

## What setup does

`python3 scripts/bootstrap.py`:

1. installs project-local Node.js 22 and npm when needed;
2. makes `node`, `npm`, and `npx` available through `~/.local/bin`;
3. installs JavaScript dependencies;
4. downloads pinned audio snapshots;
5. creates the browser and offline Android assets;
6. runs data, audio, and bundle validation.

After the first setup, open a new terminal. To activate npm immediately in the
current zsh session:

```bash
source ~/.zprofile
```

Confirm the installation with:

```bash
node --version
npm --version
```

## Use the trainer

1. Press **Native** to hear the hidden word.
2. Choose a tone for each syllable.
3. Review the word, pinyin, definition, and expected tone pattern.
4. Use **Record me** to record and compare your pronunciation.
5. Switch **Comparison voice** to compare reference and human tone audio.

Microphone permission is only required for recording. Listening and quizzes
continue to work if permission is denied.

### Audio controls

| Control | What it plays |
| --- | --- |
| **Native** | A real `audio-cmn` recording of the complete word |
| **Tone buttons** | A tone-specific syllable with reviewed fallbacks |
| **Comparison voice: Reference** | Clear public-domain pinyin recordings |
| **Comparison voice: Human** | Human `audio-cmn` syllable recordings |
| **Me** | Your most recent recording |
| **Overlay** | The native recording and your recording together |

Native words and tone buttons intentionally use separate audio pipelines.

## Tone model

The trainer distinguishes dictionary tones from tones heard in connected
speech:

| Field | Meaning |
| --- | --- |
| `lexical_pattern` | Dictionary tones, such as `3-3` for 你好 |
| `default_surface_pattern` | Expected spoken realization, such as `2-3` |
| `sandhi_tags` | Applied third-tone, 不, 一, and neutral-tone rules |
| `surface_label_needs_clip_review` | Pronunciation depends on prosodic grouping |

A recording-specific `surface_pattern` takes priority because the quiz should
grade what the selected speaker actually says. Neutral tone is represented by
`N`.

Implemented preprocessing includes:

- `3 + 3 → 2 + 3`
- 不 changing from tone 4 to tone 2 before tone 4
- 一 changing to tone 2 before tone 4
- 一 changing to tone 4 before tones 1–3
- common neutral-tone reductions

## Included data

- 11,092 HSK vocabulary entries
- 8,596 isolated native word recordings
- 1,707 human tone-specific syllable recordings
- 1,622 public-domain reference syllables

Large generated directories such as `audio/`, `www/`, and `node_modules/` are
not committed. The bootstrap recreates them from pinned sources.

## Audio quality

Known bad or mislabeled clips are quarantined in
[`data/correction_audio_quality.json`](data/correction_audio_quality.json).
The app selects an independent fallback and excludes a tone when no verified
replacement exists.

The audit checks:

- duplicate audio payloads;
- missing, malformed, or unreachable files;
- suspicious silence and padding;
- Praat and pYIN pitch contours;
- fallback selection in both comparison modes;
- browser and Android bundle completeness.

Public comparison clips receive light EQ, loudness normalization, and peak
limiting without changing pitch or timing.

## Android

Android builds require:

- JDK 21
- Android SDK Platform 36
- Android Platform Tools
- Android Build Tools 35
- a completed first-time setup

Build a debug APK:

```bash
npm run android:debug
```

Output:

```text
android/app/build/outputs/apk/debug/app-debug.apk
```

The debug APK is approximately 185 MiB and works offline.

For release signing and device installation, see
[`docs/android.md`](docs/android.md).

## Common commands

Run commands from the project directory.

| Command | Purpose |
| --- | --- |
| `python3 scripts/bootstrap.py` | Complete first-time or resumable setup |
| `python3 scripts/serve.py` | Start the browser app |
| `npm run verify:setup` | Validate an existing setup without downloading |
| `npm test` | Run all tests |
| `npm run build:mobile` | Rebuild the offline `www/` bundle |
| `npm run android:debug` | Build a debug APK |
| `npm run android:release` | Build a signed release APK |

Reuse the current `node_modules` directory:

```bash
python3 scripts/bootstrap.py --skip-npm-ci
```

Use more download workers:

```bash
python3 scripts/bootstrap.py --workers 12
```

## Project layout

| Path | Contents |
| --- | --- |
| `app/` | Browser interface and playback logic |
| `android/` | Capacitor Android project |
| `config/` | Pinned toolchain and source snapshots |
| `data/` | Reviewed vocabulary, definitions, recordings, and quality policy |
| `docs/` | Android and data-maintenance guides |
| `scripts/` | Setup, download, import, audit, and build tools |
| `tests/` | Data, audio-policy, and bundle tests |
| `audio/` | Downloaded audio generated by setup |
| `www/` | Generated offline mobile bundle |

The canonical reviewed inputs are:

- `data/hsk_words.json`
- `data/definitions.json`
- `data/recordings.json`
- `data/correction_audio_quality.json`

For snapshot updates, imports, and corpus tools, see
[`docs/audio-maintenance.md`](docs/audio-maintenance.md).

## Troubleshooting

| Problem | Action |
| --- | --- |
| `npm: command not found` | Run `python3 scripts/bootstrap.py`, then open a new terminal |
| FFmpeg is missing | Install FFmpeg and rerun the bootstrap |
| Download was interrupted | Rerun the bootstrap; valid files are retained |
| Snapshot hash mismatch | Remove only the named generated file and rerun setup |
| Mobile bundle is stale | Run `npm run build:mobile`, then `npm run verify:setup` |
| Port 8000 is busy | Run `PORT=8001 python3 scripts/serve.py` |
| Android SDK not found | Set `ANDROID_HOME` or `sdk.dir` in `android/local.properties` |

## Sources and licenses

- Definitions come from
  [CC-CEDICT](https://www.mdbg.net/chinese/dictionary?page=cedict), maintained
  by MDBG and distributed under
  [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
- Native word and human syllable recordings come from `audio-cmn` under its
  published CC BY-SA terms.
- Reference syllables come from
  [`mp3-chinese-pinyin-sound`](https://github.com/davinfifield/mp3-chinese-pinyin-sound)
  under the Unlicense.
- The Forvo inventory tool does not download or cache Forvo API audio.
