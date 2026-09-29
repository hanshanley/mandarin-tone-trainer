<div align="center">
  <img src="resources/logo.svg" width="120" alt="Mandarin Tone Trainer logo">
  <h1>Mandarin Tone Trainer</h1>
  <p><strong>Hear the word. Identify the tones. Compare your voice.</strong></p>
  <p>An offline Mandarin listening trainer for HSK 1–9 vocabulary and publisher pronunciation drills.</p>
  <p>
    <a href="#quickstart">Quickstart</a> ·
    <a href="docs/android.md">Android guide</a> ·
    <a href="docs/audio-maintenance.md">Audio guide</a>
  </p>
</div>

---

## Learn tones from real words

**Practice combines the checked direct-recording library with source-linked publisher drills.**
Original sources retain their acoustic/listening gates. Glossika examples use
explicit publisher labels and verified complete-utterance mappings, not invented
human reviews or acoustic certificates. All quiz items require playable
**tones 1, 2, 3, and 4 for every syllable**. The app checks all four
recordings before presenting the question, and all four buttons remain usable
after an incorrect guess. Entries without complete comparisons remain in the
library but are not shown in testing. Neutral comparisons use genuine whole-word
context, not an invented fifth isolated recording.
The experimental native-agreement model is not a blanket quiz admission gate.
Audio hashes are checked before the question is shown. These checks reduce
errors; they are not a guarantee of 100% pronunciation accuracy.

Mandarin Tone Trainer hides the written word and plays a native recording.
You identify the tone of each syllable before seeing the word, pinyin,
definition, and expected spoken-tone pattern.

<p align="center">
  <img src="docs/assets/trainer.png" width="900" alt="Mandarin Tone Trainer showing a completed listening question">
</p>

### Listen without visual hints

**Play audio** replays a recording of the complete word. The answer stays
hidden until you choose a tone for every syllable. Practice uses direct word
recordings, not fragments cut from sentences or syllables stitched together.
If your browser blocks automatic audio, tap **Play audio** to start.

Choose **Word length** at the top of the exercise. Each syllable has one compact
row of five tone choices, directly below replay. The replay bar stays within
reach while you scroll through longer words; replay and navigation bring the
answer area back into view. Grading reveals feedback below the choices without
scrolling them away. On phones, **Previous** and **Next word** remain at the
bottom of the screen.

Open **Practice settings** and use **Word recordings** to practice with **All sources**, the **Original
library**, **Mandarin Native**, **Sinosplice**, or **Glossika** specifically. This chooses the complete-word
audio; **Comparison voice** separately controls the isolated tone examples.

### Compare tones directly

Tone buttons use screened isolated-syllable examples when available. Switch
**Comparison voice** to choose reference-corpus or human `audio-cmn` recordings.
**Mandarin Native** is also available under **Comparison voice** in local-use builds. Each button
selects its own suitable syllable/tone recording; if that voice lacks a checked
clip, the app uses an available recording from another source. This never
changes the initial whole-word recording or stitches syllables together.
Recordings need a qualifying acoustic/listening assessment or an explicit
publisher-source mapping. Suitable single-syllable recordings can supply a fallback;
testing excludes words that would leave any of the four comparison buttons silent.
Neutral tone is demonstrated in a checked whole word, with the relevant
syllable identified, rather than an invented isolated fifth-tone clip.

Legacy isolated quiz prompts and comparisons undergo the **same citation-clarity
checks across their sources and all four tones**. These require continuous,
agreeing pitch tracks and an appropriate level, rising, dipping, or falling
contour. A low or mostly falling third tone is not automatically a source error,
but unclear citation forms are withheld from isolated teaching roles. Original
labels and downloaded files remain intact.
Glossika's separately identified source-label route retains the publisher's
five-tone row order and original audio, rather than treating uncertain
recognition as evidence of a wrong source label.
The retained library currently has **6,166 entries**; **5,465 catalog entries**
meet the complete-comparison requirements for testing. These include **3,577
Glossika entries** and original-source items unlocked by the added references.
The per-file [pronunciation review](data/quiz_pronunciation_review.json) covers
every active native, comparison, and contextual-neutral recording. It records
the applicable acoustic or publisher-mapping evidence, not a claim of
independently certified linguistic accuracy.

### Practice your pronunciation

Open **Practice your pronunciation** and use **Record me** to capture your voice
locally. **Play mine** plays it on its own, or use
**Overlay** to compare it with the native recording. Microphone permission is
only required for recording. The panel stays open while permission, recording,
or finalization is in progress so **Stop** cannot disappear.

Playback reduces excessive peaks without changing pitch, timing, or source
files. Overlay reserves headroom for both voices. Tone examples retain every
decoded speech sample, with short boundary ramps confined to the added padding
to avoid clicks. The [playback audit](docs/playback-integrity.json) records the
measured scope and the limits of these checks.

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

Sinosplice Tone Pair Drills are credited to
[John Pasden and Sinosplice.com](https://www.sinosplice.com/learn-chinese/tone-pair-drills)
under [CC BY-NC-SA 2.5](https://creativecommons.org/licenses/by-nc-sa/2.5/).
`npm run download:sinosplice` restores the pinned 90-file archive without
changing source audio or granting new approvals. Qualified files use the same
phonetic, tone, citation-clarity, and complete-comparison gates as other sources.
They are available only in local-use builds, not generic redistributable bundles.

### Individual Glossika examples and companion lessons

Local-use builds include **117 complete Glossika book lessons**: 2 consonant
lessons, 35 syllable/vowel lessons, 16 two-tone lessons, and 64 three-tone lessons.
Open **Glossika book lessons** below the practice settings, choose a lesson,
and choose either the full lesson or an individual example. The player stays
with the corresponding original book page. **Practice this example** selects
that exact eligible item in the main quiz, with all four tone comparisons.

The pinned book contains **4,570 identifiable examples**: 1,915 single-tone
exercises, 977 two-syllable entries and 1,678 three-syllable entries. This differs
from the advertised 5,055; the discrepancy is recorded, not filled with invented
items. **4,526 examples have individual playback mappings**. The remaining 44
stay indexed with explicit unresolved reasons and full-lesson access.

Individual playback uses sample-accurate intervals containing complete spoken
drill utterances and silence margins. Original MP3s are unchanged; no physical
per-item recording files, sentence-word crops or synthesized syllable sequences
are created. Solo neutral drills, unresolved source conflicts and unverified
connected-speech tone changes remain listening-only rather than receiving guessed
quiz answers. Entries also need complete comparison families before grading.
Lessons pause when the panel closes or quiz/personal-recording playback starts.
The original audio and PDF remain unchanged, with hash verification and
attenuation-only playback protection.

`npm run download:glossika` restores the publisher-linked
[companion archive](https://glossika-saas.s3-ap-northeast-1.amazonaws.com/free-download/Glossika+Tone+Training.zip)
and [original PDF](https://d310pm6npapqqb.cloudfront.net/free-download/Glossika%20Chinese%20Pronunciation%20%26%20Tone%20Training.pdf).
Install the pinned `requirements.txt` dependencies first for exact book-page
rendering. The original PDF's printed page 4 links to this archive; a
print-to-PDF copy can lose that hyperlink.

`npm run audio:glossika-examples` reconstructs the individual catalog from the
original PDF and committed utterance mappings; it does not need speech models.
Normal setup runs this automatically. The full extracted index and
`data/glossika_practice.json` are generated personal-use data, deliberately
excluded from Git and generic redistributable builds. Detailed counts are in
`data/glossika_example_coverage.json`.

Michael Campbell / Glossika, *Chinese Pronunciation & Tone Training*, copyright
2018 Glossika, all rights reserved. This is a **personal-use companion**, not a
redistribution license. Full recordings, the original PDF, and book pages remain
together and are excluded from generic redistributable builds. Neither source
publication nor these integrity checks certify every pronunciation independently.
