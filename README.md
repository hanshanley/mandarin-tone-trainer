<div align="center">
  <img src="resources/logo.svg" width="120" alt="Mandarin Tone Trainer logo">
  <h1>Mandarin Tone Trainer</h1>
  <p><strong>Hear the word. Identify the tones. Compare your voice.</strong></p>
  <p>An offline Mandarin listening trainer for HSK 1–9 vocabulary.</p>
  <p>
    <a href="#quickstart">Quickstart</a> ·
    <a href="docs/android.md">Android guide</a> ·
    <a href="docs/audio-maintenance.md">Audio guide</a>
  </p>
</div>

---

## Learn tones from real words

**Practice uses the acoustically checked direct-recording library.**
Items require matching syllable identity, usable spoken-tone evidence, and
qualifying correct-tone references. The experimental native-agreement model is
diagnostic: its uncertainty does not silently remove otherwise eligible items.
Unresolved comparison clips are withheld; missing alternative examples do not
disable a checked question.
Audio hashes are checked before the question is shown. These checks reduce
errors; they are not a guarantee of 100% pronunciation accuracy.

Mandarin Tone Trainer hides the written word and plays a native recording.
You identify the tone of each syllable before seeing the word, pinyin,
definition, and expected spoken-tone pattern.

<p align="center">
  <img src="docs/assets/trainer.png" width="900" alt="Mandarin Tone Trainer showing a completed listening question">
</p>

### Listen without visual hints

**Native** playback uses a recording of the complete word. The answer stays
hidden until you choose a tone for every syllable. Practice uses direct word
recordings, not fragments cut from sentences or syllables stitched together.
If your browser blocks automatic audio, tap **Native** to start.

Use **Word recordings** to practice with **Both sources**, the **Original
library**, or **Mandarin Native** specifically. This chooses the complete-word
audio; **Comparison voice** separately controls the isolated tone examples.

### Compare tones directly

Tone buttons use screened isolated-syllable examples when available. Switch
**Comparison voice** to choose reference-corpus or human `audio-cmn` recordings.
**Mandarin Native voice** is also available in local-use builds. Each button
selects its own suitable syllable/tone recording; if that voice lacks a checked
clip, the app uses an available recording from another source. This never
changes the initial whole-word recording or stitches syllables together.
Only recordings with qualifying acoustic evidence or explicit listening
approval can play. Clear single-syllable word recordings can supply a fallback;
unavailable alternatives remain selectable answers without playing unsafe audio.
Neutral tone is demonstrated in a checked whole word, with the relevant
syllable identified, rather than an invented isolated fifth-tone clip.

### Practice your pronunciation

Use **Record me** to capture your voice locally. Play it on its own or use
**Overlay** to compare it with the native recording. Microphone permission is
only required for recording.

## Quickstart

You need Git, Python 3.10 or newer, and about 1 GiB of free disk space.

```bash
git clone https://github.com/hanshanley/mandarin-tone-trainer.git
cd mandarin-tone-trainer
python3 scripts/bootstrap.py
python3 scripts/serve.py
```

Open **<http://localhost:8000/app/>**.

The bootstrap is the only setup command required on a fresh clone. It installs
pinned local versions of Node.js, npm, JDK 21, and FFmpeg; downloads the audio
collections; builds the offline assets; and validates the finished setup.
Downloads are resumable.

After setup, new terminals can use `node`, `npm`, `npx`, `java`, `keytool`, and
`ffmpeg` normally. To activate them immediately in the current zsh session:

```bash
source ~/.zprofile
```

To run the trainer again later:

```bash
cd mandarin-tone-trainer
python3 scripts/serve.py
```

## Tone-aware grading

The trainer distinguishes dictionary tones from tones heard in connected
speech. For example, 你好 has the lexical pattern `3-3`, while its usual spoken
realization is approximately `2-3`.

The data models common third-tone sandhi, 不 and 一 changes, and neutral-tone
reductions. Recording-specific labels take priority when the speaker's actual
pronunciation differs from the default prediction.

After an answer, **Heard here** shows pinyin marked with the recording's spoken
tones. The listed form is shown separately when it differs, so sandhi and
neutral-tone variants are not presented as contradictory answers.

## Offline Android app

The local Android debug build packages the same selected original and imported
word recordings used in browser practice. It is for local use, not redistribution.
The release build includes only recordings with established reuse metadata.

After the quickstart, install Android SDK Platform 36, Platform Tools, and
Build Tools 35. Rerun the bootstrap once so it can configure the SDK and expose
`adb`, then build:

```bash
python3 scripts/bootstrap.py --verify-only
npm run android:debug
```

The APK is created at:

```text
android/app/build/outputs/apk/debug/app-debug.apk
```

See the [Android guide](docs/android.md) for SDK configuration, release
signing, installation, and updates.

## Audio and vocabulary

The source dataset contains:

- **11,092** HSK vocabulary entries
- **2,346** additional source-derived word and character readings
- **8,596** isolated native word recordings
- **1,707** human tone-specific syllables
- **1,622** public-domain reference syllables
- **2,944** Mandarin Native recordings imported for local review: **869**
  standalone word clips and **2,075** contextual sentence clips
- **4,396** Explore vocabulary entries linked to their sentence recordings;
  these are vocabulary entries, not 4,396 separate isolated-word recordings

One-, two-, and **3+ syllable** practice uses qualifying direct recordings.
Standalone one-character imports can also supply local tone-button comparisons.
Previously generated sentence excerpts are retained for investigation only;
they are excluded from practice, normal setup, and the mobile bundle.
Only lengths represented in the usable pool appear in the syllable selector.
One-, two-, and longer-word exercises are available when their direct recordings
meet the acoustic checks.

Large audio assets are intentionally excluded from Git. The bootstrap
recreates the original corpora from pinned upstream revisions and restores
Mandarin Native's direct word recordings from recorded URLs and SHA-256 hashes.

Known audio defects and candidate fallback mappings live in
[`data/correction_audio_quality.json`](data/correction_audio_quality.json).
Audits screen file integrity, duplicate payloads, and pitch contours; they do
not certify correctness. Machine decisions live in
[`data/acoustic_reviews.json`](data/acoustic_reviews.json); optional human
attestations remain separate in [`data/audio_reviews.json`](data/audio_reviews.json).
Ambiguous or contradictory audio is withheld rather than assigned guessed labels.

`data/practice_selection.json` is a historical diagnostic preview, not a runtime
allowlist. The app and mobile bundle do not load it. Normal setup restores the
acoustically checked library without retraining or imposing experimental
agreement-model exclusions.

## Development

```bash
npm test                  # run the test suite
npm run verify:setup      # validate downloaded and generated assets
npm run build:mobile      # rebuild the offline web bundle
npm run android:debug     # build the Android debug APK
```

Application code lives in `app/`, reviewed runtime data in `data/`, tooling in
`scripts/`, tests in `tests/`, and the Capacitor project in `android/`.

Corpus updates, local imports, and individual audit tools are documented in
the [audio maintenance guide](docs/audio-maintenance.md).

The separate [calibrated pronunciation judge](docs/pronunciation-judge.md) trains
on expert-scored reference audio, with disjoint speakers for model fitting,
calibration, threshold selection, and final evaluation. It remains separate
from practice admission until its measured accuracy and domain transfer support
using it for that purpose.

The [native tone-label validator](docs/native-tone-validator.md) is the separate
label-aware workflow for the direct-recording library. It makes label-blind
audio predictions, keeps original labels as uncertain hypotheses, and reports
held-out-source results without treating source-label agreement as certified
accuracy. It is not a standalone native-library admission gate and does not
impose an additional blanket exclusion on normal practice. A narrow additive
route combines high-confidence, unseen imported whole-word predictions with
an already screened original recording of the same reading; see the maintenance
guide for the full corroboration requirements.

The [mixed-source playback improvement plan](docs/mixed-source-playback-plan.md)
is implemented by the additive comparison-bank workflow in the
[audio maintenance guide](docs/audio-maintenance.md). Initial word audio and
individual tone examples can come from different sources; coverage and
unresolved candidates are recorded per syllable/tone slot.

## FAQ

<details>
<summary><strong>Why are Native and the tone buttons different recordings?</strong></summary>

Native playback trains recognition of a complete word in natural speech. Tone
buttons use one syllable in one tone. A screened single-syllable word can serve
as a reference, but a multi-syllable word or sentence cannot.

</details>

<details>
<summary><strong>Why does the app sometimes switch audio sources?</strong></summary>

Some upstream recordings are duplicated, mislabeled, or acoustically unclear.
The quality policy proposes a clip from the independent corpus when
the preferred recording is quarantined. It can play only with matching
acoustic evidence or listening approval.

</details>

<details>
<summary><strong>What if npm, keytool, or FFmpeg is not found?</strong></summary>

Rerun `python3 scripts/bootstrap.py`, then open a new terminal. In the current
zsh session, run `source ~/.zprofile`.

</details>

<details>
<summary><strong>Where are the downloaded audio files?</strong></summary>

Generated audio is stored under `audio/`. The offline mobile bundle is written
to `www/`. Both directories are ignored by Git and can be recreated by the
bootstrap.

</details>

## Sources and licenses

Definitions come from
[CC-CEDICT](https://www.mdbg.net/chinese/dictionary?page=cedict), maintained by
MDBG and distributed under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).

Native word and human syllable recordings come from `audio-cmn` under its
published CC BY-SA terms. Reference syllables come from
[`mp3-chinese-pinyin-sound`](https://github.com/davinfifield/mp3-chinese-pinyin-sound)
under the Unlicense.

Mandarin Native recordings are separate local review imports. Their URLs
and hashes are preserved in `data/mandarin_native_recordings.json`; reuse
permission is unverified, so they are not included in distributable audio
bundles. Acoustically screened standalone clips can be used for local browser
practice. Sentence recordings and extracted fragments are not used for initial
playback or tone-button examples.
