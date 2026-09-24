# Mixed-source tone practice: focused improvement plan

Status: implemented as an additive mixed-source bank. See
`docs/audio-maintenance.md` and the generated `data/mixed_audio_coverage.json`
for exact current results and unresolved candidates. Existing examples are
preserved; this document's baseline describes the pre-change state.

## Delivered local-use expansion

| Measure | Before | After |
|---|---:|---:|
| Usable vocabulary entries | 1,736 | 2,250 |
| Initial word/recording examples | 1,814 | 2,445 |
| Imported initial-playback files | 119 | 184 |
| Imported comparison files across voice preferences | 3 | 136 |
| Playable slots for the original 298 syllables | 836 / 1,192 | 880 / 1,192 |
| Original syllables with all four comparison tones | 74 | 102 |

No baseline entry or initial recording pair was removed. Forty-four original
gaps were filled; the remaining 312 are explicit in the candidate report.
Newly usable entries introduce another 20 syllables, so expanded-pool coverage
is reported separately: 922 of 1,272 slots, with 350 unresolved.

Whole-word screening now consumes the same validated mixed comparison bank
as playback and retains the supplemental assessments when rebuilt. This
follow-on fix recovers another 84 entries and 88 initial choices beyond the
first expansion, without lowering tone thresholds. Staged updates preserve
every current initial choice in both local-use and redistributable builds.

The additional evidence includes family/decoded-duplicate-held-out isolated
predictions, independent-source corroboration, targeted independent phonetic
recognition and a narrow corroborated whole-word route. This is an implemented
coverage improvement, **not a claim of 100% independently certified linguistic
accuracy**.

## Problem to solve

Preserve a full, useful tone trainer while ensuring that the initial word and
each comparison button play appropriate, correctly mapped recordings.
The sources do not need to match. A word can come from Mandarin Native while
its syllable's tone-1/2/3/4 examples come from any suitable direct source.

Do not repeat the prior mistake of intersecting whole experimental-model
approval lists and shrinking practice to a few hundred entries. An uncertain
model result is not proof that source audio is incorrect.

## Measured baseline, 2026-09-24

These figures are measured from `loadReviewData`, `validateLedger`,
`practiceInventory`, and actual `AudioReview.correctionSelection` results.
They describe the local-use browser/debug app, not the rights-filtered release.

| Item | Current count |
|---|---:|
| Usable vocabulary entries | 1,736 |
| Initial word/recording examples | 1,814 |
| Downloaded Mandarin Native standalone files | 869 |
| Distinct Mandarin Native files used for initial playback | 119 |
| Initial vocabulary/recording examples using those files | 211 |
| Distinct Mandarin Native files selected for tone comparisons | 3 |
| Distinct Mandarin Native files reachable in any role | 122 |
| Base syllables appearing in the usable pool | 298 |
| Required tone-1-4 slots for those bases | 1,192 |
| Currently playable slots | 836 |
| Slots with a downloaded candidate | 1,192 |
| Slots with a candidate not explicitly quarantined | 1,191 |
| Bases with all four comparison tones currently playable | 74 |
| Bases with at least one comparison gap | 224 |

The three selected imported comparison keys are `kai1`, `nian2`, and `suan4`.
Examples of imported initial audio include 班, 杯子, 茶, 出, 还有, and 回家.
The measurements are saved locally in:

- `.audit/source-usage-for-improvement-plan.json`
- `.audit/comparison-coverage-gaps-for-plan.json`

Downloaded/non-quarantined is not synonymous with verified pronunciation.
The numbers establish that most comparison gaps are admission/mapping
problems to investigate, not absent downloads.

## Accuracy: distinguish the claims

File hashes and browser decoding establish which recording played and whether
it is intact. Source metadata proposes its pronunciation. Acoustic models add
evidence, but existing models have false rejections and source-transfer limits.
None of these alone establishes universal 100% linguistic correctness.

The goal is no known misleading examples, exact audio/label/playback bindings,
and independent validation of disputed or newly admitted reference families.
Record uncertainty honestly. Never invent expert reviews, silently relabel
recordings, or call held-out source-label agreement independent gold accuracy.

## 1. One source-agnostic recording catalog

Build a catalog of every direct recording already available, not just items
that passed a previous experimental gate. Extend the existing manifests and
helpers into one coherent view; do not duplicate the audio or introduce another
competing eligibility ledger. Preserve:

- original source key and label, source URL/revision and file hash;
- full word and reading where established, or a pinyin-only candidate identity;
- base syllables, lexical tones, possible spoken patterns and role suitability;
- speaker identity when actually known;
- known defects, conflicting evidence and the exact reason for exclusion;
- local-use versus redistributable rights scope.

Word labels and comparison labels are separate identities. Homophones may
share a pronunciation recording but not fabricated definitions. Preserve
reading-specific dictionary matches and explicit sandhi/neutral distinctions.

Normalize source encodings centrally. For example, Mandarin Native's `ba2i`
encodes the accented vowel; it must resolve to the same canonical syllable/tone
key as `bai2` elsewhere. Do not guess ambiguous syllable boundaries.

## 2. Separate the initial-word selector from the comparison bank

### Initial playback

Select an intact word recording matching the chosen word reading and spoken
pattern from either source. The Word recordings control remains a preference
or explicit source choice. No concatenation of character clips, no sentence
cuts, and no dependency on the comparison speaker being the same.

### Tone-button playback

Maintain `base + tone -> ordered recording candidates` across all direct
sources. Direct syllable recordings and suitable standalone single-character
recordings may fill slots. Multi-syllable words and sentences may not.

Select per slot, not per source family. For example, an initial word may use
Mandarin Native, tone 1 an original syllable file, tone 2 an imported character
file, tone 3 a public-pinyin file, and tone 4 another suitable direct recording.
This is mixing recordings, not synthesizing a new utterance.

Prefer a coherent speaker set when quality is equivalent, but never choose a
known-bad or mismatched clip merely to preserve one voice. Use only loudness/DC
normalization and existing boundary padding for playback; no pitch shifting,
time stretching, or artificial generation of missing tones.

The current selector already permits some cross-source fallbacks. The repair is
to populate and validate its full candidate bank systematically, not replace
it with another source-wide filter.

## 3. Resolve the 356 comparison gaps before building another judge

Produce an explicit gap table for every missing canonical key:

1. Every available direct candidate, source annotation and prior decision.
2. Whether the problem is bad metadata, an absent mapping, a confirmed defect,
   acoustic uncertainty, missing phonetic recognition, or a rights/build limit.
3. Independent recordings of the same syllable/tone from another source.
4. A proposed preferred clip and the specific evidence for that choice.

Start with gaps that affect many existing exercises, then third-tone coverage
and the user's reported confusions. `ba3`, `ba4`, `bei2`, `bei3`, and `qu3` already
have downloaded candidates; do not tell the learner those recordings do not
exist just because a heuristic rejected them.

Re-evaluate candidates against their original annotations, family-level tone
contrasts, expected identity, signal quality and independent references.
Acoustic evidence should identify actual contradictions and uncertain cases,
not force every natural recording into a rigid textbook contour.

Agreement across copies of the same underlying recording is not independent
evidence. Track decoded duplicates. Disagreements go to bounded,
reading-specific adjudication or replacement with a clearer direct recording;
they are not silently promoted just to fill a count.

Completion criterion: every current practice syllable has playable 1/2/3/4
references, or a precise unresolved list identifies which trustworthy
recordings still need to be obtained. Do not remove existing exercises to make
the completion percentage look better.

## 4. Expand use of the imported standalone words

Account for all 869 downloaded standalone files, including those not currently
used. Separate:

- complete word audio eligible for initial playback;
- one-syllable audio eligible for the comparison bank;
- useful alternative voices for already covered words;
- ambiguous identities/readings, known defects, and unvalidated cases.

Reassess the excluded files by their specific failure reason. Neither
"from Mandarin Native" nor "the model disagreed" is a complete correctness
decision. The original labels remain useful evidence but not absolute truth.

Expand the usable pool incrementally after each checked mapping change, showing
added/removed word IDs, affected tone slots and source-specific counts in the
developer report. Preserve existing valid examples, word-length settings,
definitions, history and recording/overlay behavior.

## 5. Neutral tone is a contextual reference

For a neutral button, play an intact, well-supported word containing the same
target syllable neutrally and identify its position. The initial recording
itself may supply that contextual example when appropriate.

Do not invent a standalone `base5` for every syllable. Many syllables have no
appropriate neutral example in the current vocabulary. Coverage of neutral
tone across genuine words and coverage of the four isolated tones are different
requirements; do not conflate them.

## 6. Release without another coverage collapse

Implement in this order:

| Stage | Deliverable | Gate |
|---|---|---|
| A | Unified direct-candidate catalog and reproducible coverage/gap report | All 869 imported files and all 1,192 current comparison slots accounted for |
| B | Canonical mixed-source comparison resolver | A clip cannot play for the wrong base/tone; preferences cannot bypass known defects |
| C | Checked replacements for the 356 comparison gaps | Evidence for each added clip; no blanket gate or silent original-label rewrite |
| D | Additional imported initial-word/alternative-voice mappings | Reading-specific identities, definitions and spoken patterns; no cuts |
| E | Complete local-use browser/Android exercise flow | Both sources actually play; exact packaged inventory; all available controls work |

Keep the current usable pool as a regression baseline. Any decrease requires
an explicit per-item defect explanation and replacement assessment, not a
global confidence-threshold change.

## Acceptance checks

- Every eligible initial recording plays its own assessed bytes.
- Every tone button uses the selected syllable/tone candidate, even when its
  source differs from the initial word or the other buttons.
- A wrong-label, duplicate, stale-hash, low-confidence or known-quarantined
  candidate cannot be admitted accidentally.
- Swapping proposed tone labels does not merely make the model repeat them.
- Complete four-tone-family coverage is counted separately from mere presence
  of all tone categories somewhere in the corpus.
- Neutral examples identify the relevant syllable in a complete word.
- Source/voice switching, rapid navigation, back history, grading, microphone
  permission, personal playback and overlay preserve the current exercise.
- Browser and local-use APK include all and only the reachable direct audio;
  redistributable packaging preserves its separate rights restrictions.
- Reports distinguish downloaded files, actually used files, vocabulary
  readings, initial-recording examples and complete comparison families.

The implementation expands the existing assessment ledger and comparison
resolver. It does not change original tone labels, cut audio, or add an
experimental blanket exclusion. Coverage gaps that lack sufficient evidence
remain explicitly reported rather than silently relabeled or hidden by
removing words.
