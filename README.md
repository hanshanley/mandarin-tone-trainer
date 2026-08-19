# Mandarin Tone Trainer

An offline Mandarin listening trainer for the browser and Android. It uses
native word recordings, tone-specific comparison audio, HSK 1–9 vocabulary,
and sandhi-aware answers.

## What it includes

- 11,092 HSK vocabulary entries
- 8,596 isolated native word recordings
- 1,707 human tone-specific syllable recordings
- 1,622 public-domain reference syllables
- lexical and spoken-tone patterns
- offline Android support
- recording and playback for pronunciation comparison

Native prompts and tone buttons use separate audio:

| Control | Audio |
| --- | --- |
| **Native** | A real `audio-cmn` recording of the complete word |
| **Tone buttons** | A tone-specific reference syllable, with reviewed fallbacks |
| **Comparison voice** | Switches all tone buttons between reference and human audio |

## Quick start

### Requirements

- Git
- Python 3.10 or newer
- FFmpeg on `PATH`
- about 1 GiB of free disk space

The setup script downloads a project-local Node.js 22 installation when a
compatible system version is unavailable. It also installs user-level
`node`, `npm`, and `npx` commands under `~/.local/bin`; administrator access
is not needed.

On macOS with Homebrew:

```bash
brew install python ffmpeg
```

### Install

```bash
git clone https://github.com/hanshanley/mandarin-tone-trainer.git
cd mandarin-tone-trainer
python3 scripts/bootstrap.py
```

The bootstrap is resumable. It installs Node dependencies, downloads pinned
audio snapshots, builds the offline bundle, and validates the complete setup.
Open a new terminal after the first setup, or activate npm immediately with:

```bash
source ~/.zprofile
```

### Run

```bash
python3 scripts/serve.py
```

Open <http://localhost:8000/app/>.

## Using the trainer

1. Listen to the hidden word with **Native**.
2. Choose a tone for each syllable.
3. Review the word, pinyin, definition, and expected pattern.
4. Use **Record me** to compare your pronunciation.
5. Change **Comparison voice** to hear either the clear reference corpus or
   human tone recordings.

The app works offline after setup. Microphone permission is only needed for
**Record me**.

## Tone handling

Each vocabulary entry contains:

| Field | Meaning |
| --- | --- |
| `lexical_pattern` | Dictionary tones, such as `3-3` for 你好 |
| `default_surface_pattern` | Expected spoken realization, such as `2-3` |
| `sandhi_tags` | Applied third-tone, 不, 一, and neutral-tone rules |
| `surface_label_needs_clip_review` | Marks pronunciation that depends on prosody |

A recording can provide its own `surface_pattern`. That clip-level label takes
priority because the quiz should grade what the selected speaker actually
says. Neutral tone is represented by `N`.

The preprocessing rules include:

- third-tone sandhi: `3 + 3 → 2 + 3`
- 不 before tone 4: `4 → 2`
- 一 before tone 4: `1 → 2`
- 一 before tones 1–3: `1 → 4`
- common neutral-tone reductions

Long third-tone sequences remain available but are marked for clip-level
review when prosodic grouping is ambiguous.

## Audio quality

Known bad or mislabeled clips are listed in
[`data/correction_audio_quality.json`](data/correction_audio_quality.json).
The app uses an independent source when a clip is quarantined and excludes the
tone when no verified replacement exists.

Audio checks cover:

- duplicate MP3 payloads
- missing or malformed files
- suspicious silence and padding
- Praat and pYIN pitch contours
- selection and fallback behavior
- browser and Android bundle completeness

Public comparison clips receive light EQ, loudness normalization, and peak
limiting. These effects do not change pitch or timing.

## Android

The Android app is a Capacitor wrapper around the same browser app. All
reachable data and audio are bundled into the APK.

### Requirements

- JDK 21
- Android SDK Platform 36
- Android Platform Tools
- Android Build Tools 35
- a completed quick-start setup

### Build a debug APK

```bash
npm run android:debug
```

Output:

```text
android/app/build/outputs/apk/debug/app-debug.apk
```

The current debug APK is approximately 185 MiB.

### Build a signed release

Create and back up a keystore outside the repository:

```bash
mkdir -p ~/.local/share/mandarin-tone-trainer
keytool -genkeypair \
  -keystore ~/.local/share/mandarin-tone-trainer/release.jks \
  -alias mandarin-tone-trainer \
  -keyalg RSA \
  -keysize 4096 \
  -validity 10000
```

Copy `keystore.properties.example` to `keystore.properties`, then enter the
absolute keystore path, alias, and passwords.

```bash
npm run android:release
```

Output:

```text
android/app/build/outputs/apk/release/app-release.apk
```

Never commit the keystore or `keystore.properties`. Android updates must use
the same signing key.

### Install on a device

With USB debugging enabled:

```bash
adb install android/app/build/outputs/apk/release/app-release.apk
```

For an update, increment `versionCode` in `android/app/build.gradle`, rebuild
with the same key, and run:

```bash
adb install -r android/app/build/outputs/apk/release/app-release.apk
```

## Common commands

| Command | Purpose |
| --- | --- |
| `npm run setup` | Run the complete resumable bootstrap |
| `npm run verify:setup` | Validate an existing setup without downloading |
| `npm test` | Run the test suite |
| `npm run build:mobile` | Rebuild the offline `www/` bundle |
| `npm run android:debug` | Build a debug APK |
| `npm run android:release` | Build a signed release APK |

To reuse the current `node_modules` directory:

```bash
python3 scripts/bootstrap.py --skip-npm-ci
```

To increase download concurrency:

```bash
python3 scripts/bootstrap.py --workers 12
```

## Repository layout

| Path | Contents |
| --- | --- |
| `app/` | Browser interface and playback logic |
| `android/` | Capacitor Android project |
| `config/` | Pinned toolchain and source snapshots |
| `data/` | Reviewed vocabulary, definitions, recordings, and quality policy |
| `scripts/` | Setup, download, import, audit, and build tools |
| `tests/` | Data, audio-policy, and bundle tests |
| `audio/` | Downloaded audio; generated and ignored by Git |
| `www/` | Generated offline mobile bundle; ignored by Git |

The canonical reviewed inputs are:

- `data/hsk_words.json`
- `data/definitions.json`
- `data/recordings.json`
- `data/correction_audio_quality.json`

Normal setup does not regenerate these files from changing upstream
vocabulary or dictionary downloads.

## Advanced audio workflows

These scripts are for reviewed data maintenance, not normal setup.

### Update a pinned audio snapshot

1. Change the 40-character revision in `config/source_snapshots.json`.
2. Download into clean generated `audio/` and `imports/` directories.
3. Review counts, hashes, duplicates, pitch contours, and speaker quality.
4. Update the expected counts and sample hashes.
5. Run `npm run verify:setup`.

### Import local human recordings

Place files under a source and exact Mandarin word:

```text
imports/
  forvo/
    公司/
      speaker-name.mp3
      speaker-name.json
```

Optional sidecar:

```json
{
  "speaker": "speaker-name",
  "sex": "f",
  "country": "CHN",
  "region": "Beijing",
  "surface_pattern": "1-1",
  "source_url": "https://example.com/source"
}
```

Import them with:

```bash
python3 scripts/import_local_audio.py
```

Imported sources are indexed, but native quiz prompts currently select only
verified `audio_cmn` recordings.

### Other tools

| Script | Purpose |
| --- | --- |
| `download_audio_cmn.py` | Download isolated native words |
| `download_audio_cmn_syllables.py` | Download human tone syllables |
| `download_public_pinyin_syllables.py` | Download public comparison syllables |
| `check_audio_cmn_syllables.py` | Check duplicate and malformed syllable audio |
| `pad_audio_cmn_syllables.py` | Export padded syllable MP3 files |
| `forvo_inventory.py` | Inventory Forvo pronunciations without caching audio |
| `common_voice_index.py` | Index Mandarin Common Voice context clips |
| `download_openai_tts_sample.py` | Create optional synthetic samples |

Generated audio, imported files, Node modules, mobile assets, SDK settings,
build output, and signing material are excluded from Git.

## Troubleshooting

| Problem | Action |
| --- | --- |
| Missing command | Install the command named in the error and rerun setup |
| Interrupted download | Rerun `npm run setup`; valid files are retained |
| Snapshot hash mismatch | Remove only the named generated file and rerun setup |
| Stale mobile bundle | Run `npm run build:mobile`, then `npm run verify:setup` |
| Port 8000 is busy | Run `PORT=8001 python3 scripts/serve.py` |
| Android SDK not found | Set `ANDROID_HOME` or `sdk.dir` in `android/local.properties` |

## Data sources and licenses

- Definitions come from
  [CC-CEDICT](https://www.mdbg.net/chinese/dictionary?page=cedict), maintained
  by MDBG and distributed under
  [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
- Native word and human syllable recordings come from `audio-cmn` under its
  published CC BY-SA terms.
- Reference syllables come from
  [`mp3-chinese-pinyin-sound`](https://github.com/davinfifield/mp3-chinese-pinyin-sound)
  under the Unlicense.
- Forvo API audio is not downloaded or cached by the inventory script.
