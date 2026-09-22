# Audio and data maintenance

These workflows maintain the source corpus and its listening approvals.
Setup downloads files; it does not approve them for practice.

## Admission to practice: fail closed

`data/audio_reviews.json` is the sole listening-approval ledger. It starts
empty because prior ASR, pitch, and duplicate screens are not listening
reviews. No existing source or fallback is grandfathered in.

Every eligible question requires:

1. Two independent, proficient Mandarin listeners approving the exact native
   recording's syllables, lexical reading, **actual spoken-tone pattern**,
   and clarity for graded practice.
2. The same independent approval for **all four comparison tones** of every
   syllable, not only the correct answer. Neutral-tone judgments are reviewed
   in the complete native word; no standalone neutral comparison is invented.
3. An exact match between the reviewed labels and current vocabulary/recording
   metadata, plus a SHA-256 match for every recording.

The browser verifies all required files before revealing answer buttons or
playing a question. Native playback, comparison playback, and overlay use
those verified bytes. A missing approval, unavailable hash API, changed file,
or failed download blocks the item. The mobile build includes only approved
audio reachable from fully reviewed questions.

This enforces a review requirement, **not a mathematical guarantee of 100%
pronunciation accuracy**. Listening judgments can still be wrong. Do not
fabricate reviewer attestations or turn automated results into approvals.
Reviewer IDs record attestations, not authenticated digital signatures;
maintainers must verify that the independent listening actually occurred.

## Export and complete the full listening-review queue

```bash
node scripts/review_audio.mjs --export .audit/listening-review.json
```

The export includes every current app-candidate native recording/reading and
comparison source, including quarantined candidates, complete metadata,
SHA-256 hashes, and review targets. It is not a metadata-only inventory of
approved clips. Audio remains locally available at each `audio_path`; use
`python3 scripts/serve.py` and open that path on the localhost server to listen.
Existing export files are not overwritten.

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
candidate remains `pending` even if it has no automated flags. Whole-comparison
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
Zero approvals is a valid, paused setup, not a successful corpus certification.

## Mandarin Native and additional sources

The app links to Mandarin Native as an **online external reference**, including
word-specific links after an answer. Its public word-audio manifest has also
been imported for local review: **869 recordings** under `audio/mandarin_native/`,
with provenance and hashes in `data/mandarin_native_recordings.json`.
Redistribution permission was not established; no license or listening approval
has been invented. No imported audio enters the offline bundle while these
requirements remain unresolved.

Restore the indexed recordings, or explicitly discover newly listed ones:

```bash
npm run download:mandarin-native
python3 scripts/download_mandarin_native.py --refresh-index
```

Normal setup restores the pinned imports too. Downloads are resumable, validate
MP3 payloads, and reject changes to already recorded hashes. Refreshing the
index adds new source keys but does not silently relabel existing recordings,
discard removed upstream keys, or approve anything.

The site places tone digits after the marked vowel (`bái` becomes `ba2i`, not
`bai2`), and has legacy Unicode names. One stale manifest filename, `dǒnɡ`,
was resolved to the site's `do3ng.mp3`; both the original key and resolved URL
are recorded. Vocabulary associations are only proposed reading matches in
`candidate_hsk_ids`, never confirmation of the recorded word or spoken tones.
Unmatched clips remain in the review export as `unmapped_native`; identify and
map their reading before creating a native approval.

For admission, document verified reuse permission, set `rights_status` to
`cleared` with the actual `license`, resolve the proposed reading, and remove
the explicit `quiz_eligible: false` quarantine only after its issue is resolved.
The exact recording/reading still needs the two independent ledger approvals.
Native playback and bundle selection support the imported source, but never
use these whole-word clips as automatic isolated-tone comparisons.

Before importing any additional source, establish permission for the exact
recordings and preserve provenance and license metadata. Contextual clips must
not be treated as isolated-tone exemplars without reviewing boundaries and
spoken tones. New sources need the same listening approvals and runtime/bundle
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

Native quiz prompts currently support explicitly listening-approved `audio_cmn`
recordings and rights-cleared, independently approved `mandarin_native`
recordings. Other locally imported sources remain index-only.

## Corpus tools

| Script | Purpose |
| --- | --- |
| `download_audio_cmn.py` | Download isolated native words |
| `download_audio_cmn_syllables.py` | Download human tone syllables |
| `download_public_pinyin_syllables.py` | Download public comparison syllables |
| `download_mandarin_native.py` | Restore hash-pinned local word-audio imports, without granting approval |
| `check_audio_cmn_syllables.py` | Check duplicate and malformed syllable audio |
| `pad_audio_cmn_syllables.py` | Export padded syllable MP3 files |
| `forvo_inventory.py` | Inventory Forvo pronunciations without caching audio |
| `common_voice_index.py` | Index Mandarin Common Voice context clips |
| `download_openai_tts_sample.py` | Create optional synthetic samples |
| `audit_native_readings.py` | Resumably screen native words for pinyin mismatches |
| `review_audio.mjs` | Export pending listening candidates and validate explicit approvals |

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
Mandarin-specific ASR confirmation; it is not listening approval. All unapproved
recordings remain unavailable for practice regardless of ASR status.

The review policy is conservative:

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
