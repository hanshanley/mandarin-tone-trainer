# Audio and data maintenance

These workflows maintain the source corpus, automated acoustic decisions, and
optional listening approvals. Setup restores the committed decisions and exact
audio snapshots; it does not fabricate new assessments.

## Admission to practice: fail closed

`data/acoustic_reviews.json` contains explicit **machine-screened** decisions.
`data/audio_reviews.json` is reserved for optional human listening attestations.
The current app uses these established acoustic/listening routes without adding
the experimental native-agreement filter on top. Hashes, exact recording-specific
labels, direct-only audio, known quarantines and correct-tone reference checks
still apply. Listening to every file is not required, and no source is accepted
just because its filename contains a tone number.

## Preview experimental agreement results without reducing practice

```bash
npm run audit:agreement-selection -- \
  --inventory .audit/native-tone-inventory.json \
  --predictions .audit/native-tone-delivery-predictions.json
```

Use the current matching native inventory and prediction export, not the example
filenames if you have regenerated those artifacts. The builder verifies model/
inventory/prediction fingerprints and audio hashes. Its diagnostic preview selects only
`reference_supported` cases with exact original-pattern agreement, at least 0.9
source-agreement confidence, at least 0.15 class margin, stable perturbed
boundaries, sufficient pitch evidence, confirmed syllable identity, no training
reference overlap, and a separately qualifying acoustic assessment. Native word
examples also need distinct selected correct-tone references.

These thresholds describe model/source agreement, not gold-validated accuracy.
The selection remains explicitly `accuracy_certified: false`. Likely mismatches,
possible alternate pronunciations, low-confidence items and missing evidence
do not become new answers. Original labels and recordings are not rewritten.
The builder requires examples from both original and imported sources and all
five tone categories across the native set.

The output defaults to `.audit/agreement-selection-preview.json` and does not
change the quiz pool. The earlier `data/practice_selection.json` snapshot is
retained only as historical evidence; it is not loaded by the app or included
in mobile builds. Intersecting that preview with the acoustic library had
removed most usable exercises without independently proving them wrong.
Runtime tests now ensure experimental agreement data cannot reduce normal
practice implicitly.

## Acoustic prerequisites

### Additive mixed-source comparison bank

The runtime comparison index combines all eligible direct sources per
`base + tone`. **Word recordings** chooses intact native-word audio;
**Comparison voice** independently prefers reference, original human, or
Mandarin Native syllables. Missing preferred-source clips fall back across the
same mixed bank. Rights-filtered builds omit the imported-voice option.

`cross-source-native-reference-v1` is an additional evidence route in the
existing `data/acoustic_reviews.json`, not a competing allowlist:

- Five cross-fit models keep each complete base family and every identical
  decoded recording out of its own training and temperature-calibration folds.
- Predictions do not receive the proposed tone as an input. Both the expected
  probability (at least 0.95) and top-class margin (at least 0.25) must qualify.
- At least two usable pitch tracks, adequate voicing, low clipping and phonetic
  identity evidence are required.
- The same syllable/tone must be supported in a genuinely different source.
  Matching decoded hashes or near-identical waveform payloads do not count as
  independent corroboration.
- Known quarantines are not overridden. A clear primary ASR disagreement on the
  base syllable cannot be rescued by a fallback recognizer.

For unresolved identity-only gaps, a separate unprompted Whisper-small check
examines both the raw audio and a cleaned, silence-padded analysis copy. The
original playback file is unchanged. Both independent checks must resolve the
same base, and the evidence preserves the original primary-model outputs and
model fingerprint. This is additional evidence, not a human attestation.

Supported comparison recordings may also serve their exactly mapped
single-syllable native word readings. Initial-word and comparison identities
remain separate, and new native entries retain a different checked comparison
recording. Existing native assessments and usable examples are preserved.

For imported two-syllable whole words, `cross-source-whole-word-v1` adds a
narrow corroborated route. It requires the label-blind native model to agree
with the exact original spoken pattern, probability at least 0.95, margin at
least 0.25, at least three stable boundary variants, sufficient signal quality,
raw/prepared phonetic identity checks and no training-reference overlap.
An already screened original-library **whole word of the same vocabulary
reading and spoken pattern** must also exist. Encoded/decoded duplicates and
near-identical copies cannot corroborate each other. The reference's independent
per-tone support remains mandatory. This route does not admit new neutral
examples or combine syllable recordings into words.

```bash
# Export immutable candidates with original labels and duplicate fingerprints.
npm run native:inventory -- --output .audit/native-tone-inventory.json

# Build additive support; the first run also records identity-only gap candidates.
npm run audio:mix -- --inventory .audit/native-tone-inventory.json

# Optional independent recognition for a specific gap inventory.
npm run audio:identity-gaps -- --inventory .audit/native-tone-inventory.json \
  --gap-keys .audit/comparison-coverage-gaps-for-plan.json
npm run audio:mix -- --inventory .audit/native-tone-inventory.json

# Compare against a saved pre-change practice inventory.
npm run audio:coverage -- --inventory .audit/native-tone-inventory.json \
  --baseline .audit/mixed-audio-original-baseline.json
```

The compiler validates proposed data through the real runtime policy before
publishing it and refuses to remove a previously usable word/initial-recording
pair in either the local-use or redistributable build. Model files are published
only after that validation succeeds. The report
in `data/mixed_audio_coverage.json` accounts for every imported
standalone file, actual selection under every comparison voice, original
coverage, added roles and unresolved slots with candidate-specific reasons.

Measured against commit `61e4066`, the local-use expansion preserves all 1,736
previously usable entries and 1,814 initial examples. It supplies 2,250 entries
and 2,445 initial examples; Mandarin Native contributes 408 of those examples
using 184 distinct files, plus 136 files selected for comparisons across the
three voice preferences. These file-role counts overlap and must not be added.
Of the original 1,192 comparison slots, 880 are playable (44 more than before);
102 original families now have all four tones, versus 74 before. The original
pool still has 312 unresolved slots. Including newly usable syllables, the
expanded pool has 922 playable slots out of 1,272 and 350 unresolved slots.
All 869 imported standalone files are accounted for.

The follow-on word recovery preserves the mixed bank and uses its references
when screening whole words. This recovered 84 additional entries and 88 initial
recording choices beyond the first expansion, without relaxing phonetic,
pitch, alignment or neutral-tone checks. A word can rely on a comparison from
either source; if its supporting comparison is local-only, that word's new
assessment is also local-only. A redistributable reference is preferred where
one qualifies.

The new models learn from uncertain source annotations. Source agreement plus
independent-recording corroboration is **not independently certified 100%
linguistic accuracy**. Unresolved candidates stay explicit; they are not called
wrong solely because a recognizer or model could not establish support.

Every eligible question requires:

1. Syllable-identity evidence and qualifying tone evidence from the spectral
   route or the corroborated mixed-source routes above. The spectral route
   requires at least two usable pitch trackers agreeing on F0 and each expected
   **spoken tone**, with no conflicting usable tracker.
   Independent ASR may return Hanzi or exact literal pinyin. Approximate
   spellings such as `jai` -> `zhai` or `lee` -> `li` are not guessed.
   Concatenated pinyin must have a unique syllable segmentation; ambiguous
   `XIAN` is not forced into either `xian` or `xi-an`.
2. A screened reference for each **correct spoken tone**. Alternative answers
   remain selectable, but play an example only when it is also screened. A
   missing alternative reference is reported explicitly after selection, not
   played unverified or used to give away the answer by disabling its button.
   Clear licensed single-syllable word clips can replace unclear corpus clips;
   a whole multi-syllable word cannot be used as a comparison.
3. An exact match between assessed labels and current vocabulary/recording
   metadata, plus a SHA-256 match for every recording.

The browser verifies all required files before revealing answer buttons or
playing a question. Native playback, comparison playback, and overlay use
those verified bytes. Missing evidence, an unavailable hash API, a changed file,
or a failed download blocks the item. The local-use Android build includes
qualifying imported audio for personal practice. The redistributable build
removes local-only decisions and files, and every bundle records its scope.

This is **not a mathematical guarantee of 100% pronunciation accuracy**.
F0, recognition, and alignment can all fail. Ambiguous contours, recognition
conflicts, unsupported neutral-tone reductions, and
single-character polyphonic readings without an explicit matching phonetic ASR
spelling are withheld. Do not label
machine screening as human review.
Reviewer IDs record attestations, not authenticated digital signatures;
maintainers must verify that the independent listening actually occurred.

## Rebuild automatic acoustic decisions

Install the optional dependencies in `requirements-audio-audit.txt`, then:

```bash
node scripts/review_audio.mjs --export .audit/acoustic-candidates.json
python3 scripts/collect_acoustic_evidence.py --candidates .audit/acoustic-candidates.json --phase profiles
python3 scripts/collect_acoustic_evidence.py --candidates .audit/acoustic-candidates.json --phase asr
python3 scripts/collect_acoustic_evidence.py --candidates .audit/acoustic-candidates.json --phase prepared-asr
python3 scripts/collect_acoustic_evidence.py --candidates .audit/acoustic-candidates.json --phase alignment
python3 scripts/build_acoustic_reviews.py --candidates .audit/acoustic-candidates.json --activate-imported
npm run audio:mix -- --inventory .audit/native-tone-inventory.json
npm run audio:coverage -- --inventory .audit/native-tone-inventory.json \
  --baseline .audit/mixed-audio-original-baseline.json
npm run audit:listening
```

The spectral compiler validates and retains the existing supplemental routes,
uses their comparison references while screening native words, and stages the
combined result through the same no-regression gate as the mixed compiler.
Rebuilding the original route no longer discards mixed-source support or
withholds otherwise screened words just because their references were admitted
through another route. If source files or labels changed, refresh the immutable native
inventory and its diagnostic predictions first; stale inputs are rejected.

The collectors resume by audio hash and evidence version. DC bias and
low-frequency contamination are removed before tracking pitch with pYIN,
Praat, and WORLD; the audio played to the learner is not pitch-shifted.
Register estimates, time-aligned contours, and unprompted ASR are kept as
separate evidence. Recognition runs on both the original file and a DC-cleaned,
high-pass-filtered, silence-trimmed copy normalized to RMS 0.1 with peak limiting.
Pitch and speaking rate are unchanged; original recordings are not overwritten.
Both recognition results are interpreted without consulting the expected label.
If they identify different syllable sequences, the clip is withheld; if one is
uninterpretable, the other may supply identity evidence. Mixed Hanzi/Latin
transcripts cannot be silently reduced to a matching suffix. The ledger retains
both transcripts, decoded sequences, file hashes, and preparation parameters.

Forced alignment is conditioned on the expected text only
**after** independent recognition matches the syllable sequence; it is not
itself used as proof of identity. Single syllables use their complete clips.

The compiler refuses stale/missing pitch or recognition evidence and does not
guess missing syllable boundaries. It records the pipeline fingerprint,
per-syllable votes, transcript, hashes, and supporting comparison clips.
Detailed exclusion reasons are written to `.audit/acoustic-decisions.json`.
`--allow-partial` is diagnostic-only and cannot write the runtime ledger or
activate imported recordings.

### Reconcile coverage instead of counting different units together

```bash
node scripts/review_audio.mjs --coverage .audit/practice-coverage.json \
  --decisions .audit/acoustic-decisions.json
```

The report distinguishes vocabulary entries, vocabulary/initial-recording
pairs, unique audio files, standalone imports, and contextual Explore vocabulary.
Its mutually exclusive vocabulary categories sum to the entire HSK vocabulary:
eligible, no isolated recording, unresolved native screening, or no correct-tone
reference. Detailed exclusion reasons can overlap when one word has multiple
candidate recordings; these counts must not be added together. Stale decision
reports are rejected by pipeline fingerprint. Omit `--decisions` to count runtime
coverage without attaching exclusion reasons. The report also assigns one
primary blocker per unavailable word, using the furthest screening stage any
of its candidate recordings reached; these primary-blocker counts are mutually
exclusive.

Coverage counts and unresolved-item diagnostics belong in this developer report,
not on the learner's practice screen. The app selects from available exercises
and displays an empty-state message only when none match the chosen filters
or no exercises are available. Downloaded sentence recordings are not counted
as isolated-word practice examples.

Known bad recordings remain quarantined. `--activate-imported` changes only
pending or previously machine-screened standalone imports; explicit rejections
and contextual sentence recordings are not promoted. Human-only overrides
remain available for genuinely unresolved exceptions.

### All five tone categories

The usable corpus and distributable build must each contain tones 1, 2, 3, 4,
and neutral (`N`); setup/build validation fails if any category is absent.
This is coverage **across items**, not an assertion that every syllable has all
five natural pronunciations.

Neutral admission additionally requires an independently attested CC-CEDICT
neutral reading, a screened full-tone anchor in the same word, reliable pitch
tracks, clear duration/intensity reduction, and pitch consistent with the
preceding tone. A lexical neutral marker alone is not sufficient. The neutral
decision cannot hide a tracker's clear full-tone result as an abstention, and
its lexical evidence must match the ledger's dictionary snapshot. A selected
neutral example is rechecked against the current word and recording metadata,
so an old assessment cannot survive a corrected source label. The neutral
button plays a checked **whole-word contextual example** and states which
syllable to listen for; no artificial standalone “fifth-tone” recording is
generated.

## Optional listening review and exception resolution

```bash
node scripts/review_audio.mjs --export .audit/listening-review.json
```

The export includes every current app-candidate native recording/reading and
comparison source, including quarantined candidates, complete metadata,
SHA-256 hashes, and review targets. It is not a metadata-only inventory of
approved clips. Audio remains locally available at each `audio_path`; use
`python3 scripts/serve.py` and open that path on the localhost server to listen.
Existing export files are not overwritten. This is also the input to automatic
screening, not a list of files that someone must individually listen to.

To attach automated evidence and put quarantined or flagged comparisons first,
without dropping any candidate or creating approvals:

```bash
python3 scripts/audit_correction_tones.py --output .audit/full-tone-screen.json
node scripts/review_audio.mjs --export .audit/prioritized-listening-review.json \
  --tone-screen .audit/full-tone-screen.json
```

The export preserves the screen's scope (`whole_comparison_corpus` versus
`selected_keys`). Findings must match the candidate's exact file hash and tone
key; stale findings abort the export. Unscreened clips stay explicit, and every
candidate remains `pending` for human attestation even if it is machine-screened.
That export status does not prevent admission via the acoustic ledger. Whole-comparison
screening does not claim to screen native word recordings.

Reviewers should independently identify the word/syllable and tones before
checking the proposed labels, then compare the complete tone family. Resolve
disagreements rather than approving by majority or guessing a label swap.
For connected speech, review the actual spoken realization and segmentation,
not merely dictionary tones. Reject clipped, unclear, or misleading examples.

For a candidate that both listeners independently approve, copy its descriptor,
`sha256`, `source_url`, and `license` into the ledger's `approvals` array, set
`status` to `approved`, and add two `reviews` objects with distinct reviewer
IDs. Each review must contain:

```json
{
  "reviewer": "<independent reviewer ID>",
  "method": "human_listening",
  "verdict": "approved",
  "reviewed_at": "<ISO date of actual review>",
  "identity_correct": true,
  "tones_correct": true,
  "clear_for_practice": true,
  "audio_sha256": "<copy review_target.audio_sha256>",
  "label_identity": "<copy review_target.label_identity>"
}
```

These targets bind each attestation to the exact bytes and labels. Changing
audio, the word reading, segmentation, or spoken-tone pattern requires fresh
review; do not carry over old attestations. If source metadata is wrong, correct
the canonical vocabulary/recording data and regenerate the candidate first.
An approval cannot override a known quarantine: resolve and document that
issue in the quality policy before admission.

```bash
npm run audit:listening    # validate attestations, labels, provenance, and hashes
npm test
npm run build:mobile
```

The coverage report explicitly states how many practice entries are eligible.
Zero qualifying assessments results in a paused setup, not a successful corpus
certification. An empty human ledger does not pause automatically screened items.

## Mandarin Native and additional sources

### Direct word and character practice

`data/mandarin_native_words.json` extends, rather than overwrites, the HSK
vocabulary with source-derived word and character readings. Exact existing
readings reuse their HSK IDs; additional readings receive stable `MN-` IDs.
Source pinyin is segmented against the syllable inventory, and unresolved
segmentation or mixed-script tokens are not guessed. Definitions are matched
by reading against a cached CC-CEDICT snapshot.

```bash
python3 scripts/build_context_words.py --phase vocabulary
```

This command builds vocabulary metadata; it does not cut or synthesize audio.
The app uses qualifying direct word recordings for one-, two-, and **3+
syllable** practice. Screened standalone one-character recordings may also
supply local tone-button comparisons. Larger words are not assembled from
character recordings.

**Sentence-extracted audio is excluded from normal practice.** The historical
`data/context_word_recordings.json` and `audio/mandarin_native/excerpts/` files
are preserved for investigation, not loaded by the app or normal analysis
pipeline. The runtime rejects them even if an old assessment exists. Normal
setup does not reconstruct them, and mobile packaging excludes both their
index and their audio.

The legacy align/extract/restore phases remain research tools, not steps for
enabling practice. Matching timestamps or a plausible pitch contour is not
enough to prove that cutting speech preserved natural word boundaries.

The app links to Mandarin Native as an **online external reference**, including
word-specific links after an answer. Both public audio collections have been
imported for local review: **869 standalone word clips** from
`audio_manifest.json` and **2,075 sentence clips** from Explore's `data.json`.
The latter contains **4,396 vocabulary entries**, using the site's nonempty
pinyin filter. Several words share each sentence recording, so the combined
inventory is **2,944 audio files**, not one file per Explore word.

The word clips live under `audio/mandarin_native/`, and sentence clips under
`audio/mandarin_native/context/`. Provenance, hashes, collection references, and
the complete `explore_vocabulary` word-to-recording index are preserved in
`data/mandarin_native_recordings.json`. MP3 and M4A containers are retained
without transcoding; sentence filenames use source-reference hashes.
Redistribution permission was not established; no license or listening approval
has been invented. No imported audio enters the offline bundle while these
requirements remain unresolved.

Restore the indexed recordings, or explicitly discover newly listed ones:

```bash
npm run download:mandarin-native
python3 scripts/download_mandarin_native.py --refresh-index
```

Normal setup and `npm run download:mandarin-native` restore only direct word
recordings (`--standalone-only`). Previously downloaded sentence files and their
metadata are retained untouched. The downloader without that option remains
available for explicit source research. Downloads are resumable,
validate container-specific MP3/M4A headers, and reject changes to already
recorded hashes. Discovery requests both datasets with the live site's cache
version and records their hashes. Refreshing the index adds new source
recordings and refreshes unverified contextual vocabulary links, but does not
change reviewed readings, discard removed upstream recordings, or approve
anything. Vocabulary entries and unique audio files are counted separately.

The site places tone digits after the marked vowel (`bái` becomes `ba2i`, not
`bai2`), and has legacy Unicode names. One stale manifest filename, `dǒnɡ`,
was resolved to the site's `do3ng.mp3`; both the original key and resolved URL
are recorded. Vocabulary associations are only proposed reading matches in
`candidate_hsk_ids`, never confirmation of the recorded word or spoken tones.
Unmatched clips remain in the review export as `unmapped_native`; identify and
map their reading before creating a native approval.

Sentence recordings appear separately as `context_native`, with their source
entry IDs and contained vocabulary. They have no isolated-word candidates and
are rejected by the word-quiz admission policy even if a word ID is assigned
later. Generated `aligned_word` clips are also excluded, regardless of legacy
assessment status. Contextual corpus membership is not an approval of an
isolated word or a tone-button example.

Screened standalone imports can be activated for local browser practice with
the compiler's `--activate-imported` option. They are explicitly marked
`distribution_scope: local_only` while reuse permission remains unverified.
Distribution additionally requires documented permission, `rights_status:
cleared`, and the actual `license`; spectral analysis cannot grant those rights.
Native playback supports qualified imported words. Only screened standalone
single-character imports can supply isolated-tone comparisons; multi-syllable
words and aligned sentence excerpts cannot.

Before importing any additional source, establish permission for the exact
recordings and preserve provenance and license metadata. Contextual clips must
not be treated as isolated-tone exemplars without reviewing boundaries and
spoken tones. New sources need the same assessments and runtime/bundle
integration; an import alone never makes a recording eligible.

## Update a pinned audio snapshot

1. Change the 40-character revision in `config/source_snapshots.json`.
2. Download into clean generated `audio/` and `imports/` directories.
3. Review counts, hashes, duplicates, pitch contours, and speaker quality.
4. Update expected counts and representative hashes in the snapshot file.
5. Run `npm run verify:setup`.

Normal setup deliberately does not regenerate canonical vocabulary,
definitions, recordings, or quality policy from moving upstream sources.

## Import local human recordings

Place audio under a source and exact Mandarin word:

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

Import the files:

```bash
python3 scripts/import_local_audio.py
```

Native quiz prompts support qualifying `audio_cmn` recordings and screened
standalone `mandarin_native` recordings for local use. Distribution has the
additional rights requirement. Other locally imported sources remain index-only.

## Corpus tools

| Script | Purpose |
| --- | --- |
| `download_audio_cmn.py` | Download isolated native words |
| `download_audio_cmn_syllables.py` | Download human tone syllables |
| `download_public_pinyin_syllables.py` | Download public comparison syllables |
| `download_mandarin_native.py` | Restore both audio collections and the complete Explore vocabulary index, without granting approval |
| `check_audio_cmn_syllables.py` | Check duplicate and malformed syllable audio |
| `pad_audio_cmn_syllables.py` | Export padded syllable MP3 files |
| `forvo_inventory.py` | Inventory Forvo pronunciations without caching audio |
| `common_voice_index.py` | Index Mandarin Common Voice context clips |
| `download_openai_tts_sample.py` | Create optional synthetic samples |
| `audit_native_readings.py` | Resumably screen native words for pinyin mismatches |
| `review_audio.mjs` | Export analysis candidates and validate human and acoustic ledgers |
| `collect_acoustic_evidence.py` | Resumably collect hash-bound pitch, ASR, and alignment evidence |
| `build_acoustic_reviews.py` | Compile explicit machine decisions and selectively activate local imports |

Automatic acoustic evidence can be collected without listening to every file:

```bash
python3 scripts/collect_acoustic_evidence.py \
  --candidates .audit/listening-review.json --phase profiles
python3 scripts/collect_acoustic_evidence.py \
  --candidates .audit/listening-review.json --phase asr
python3 scripts/collect_acoustic_evidence.py \
  --candidates .audit/listening-review.json --phase prepared-asr
python3 scripts/collect_acoustic_evidence.py \
  --candidates .audit/listening-review.json --phase alignment
```

Profiles remove DC bias and low-frequency contamination before running pYIN,
Praat, and WORLD. Tracks retain their original time coordinates; missing
portions are not silently collapsed into a different contour. ASR is run
without the expected word as a prompt, and results are matched by file key and
content hash rather than trusting batch order. Neither these measurements nor
ASR transcripts are human-listening attestations.

## Audit native reading identity

Tone-contour analysis cannot detect a wrong base syllable such as `hai`
substituted for `ke`. Install the optional ASR dependencies and run the
resumable whole-corpus screen:

```bash
python3 -m pip install -r requirements-audio-audit.txt
python3 scripts/audit_native_readings.py
```

Results are appended to the ignored `.audit/native-readings.jsonl` file after
every recording. A `review` result is a candidate for independent listening or
Mandarin-specific ASR confirmation; it is not listening approval. An old ASR
status alone does not admit a clip: admission uses the current content-bound
acoustic ledger or explicit human approval.

For additional investigation of unresolved identity cases:

1. Whisper screens every app-relevant native recording.
2. Mandarin Paraformer independently checks Whisper review candidates.
3. Same-speaker syllable matching supplies additional evidence for disputed
   initials, finals, and polyphonic readings.
4. Only confirmed mismatches receive a replacement or `quiz_eligible: false`;
   model disagreements remain available for human review.

Screen every tone-specific comparison clip:

```bash
npm run audit:tones
```

This command fails when a required comparison candidate has an unresolved
contour flag, missing pitch track, or tracker disagreement that is not already
quarantined in `data/correction_audio_quality.json`. That failure identifies
review work; it does not mean a label should be automatically swapped.
The audit uses three independent pitch trackers: pYIN, Praat autocorrelation,
and WORLD/Harvest. It includes rising-only third-tone screening, but never
assumes a flat/low third tone must be wrong or that second/third tones can be
certified from contour shape alone. Even a result without flags is labeled
`screened_only`, never `approved`.

For a clearly scoped investigation, without claiming whole-corpus coverage:

```bash
python3 scripts/audit_correction_tones.py --keys zhai2 zhai3 \
  --output .audit/zhai-tone-screen.json
```

Run the complete contour and re-encoded-duplicate checks together:

```bash
npm run audit:corrections
```

Screen every tone-specific clip for a base-syllable mismatch:

```bash
python3 scripts/audit_correction_identity.py
```

The identity audit is resumable and supports the same `--completed-from`,
`--shard-count`, and `--shard-index` options as the native-reading audit.
Confirm its `review` candidates with Mandarin Paraformer using
`scripts/confirm_correction_identity.py`.

Prioritize single-character homographs with:

```bash
python3 scripts/audit_native_readings.py \
  --polyphonic-single-character \
  --output .audit/polyphonic-readings.jsonl
```

For a faster resumable audit, split the unfinished recordings into independent
outputs using `--completed-from`, `--shard-count`, and `--shard-index`. Merge
the JSONL files after every shard completes.

Correction playback always uses tone-specific syllable recordings, never an
unlabeled word recording. Ambiguous Hanzi readings require matching recording
metadata before they can enter the quiz.

The browser adds 120 ms of leading silence and 200 ms of trailing silence to
decoded correction audio in memory. It copies decoded PCM without changing
pitch, timing, or consonant distinctions.
