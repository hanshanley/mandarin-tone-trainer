# Native tone-label validator

This implements the label-aware native-library workflow in
`tone-validation-redesign.md`. It is separate from the learner-pronunciation
judge and from production practice admission.

**Implemented:** an immutable direct-recording hypothesis inventory,
label-blind single-syllable and intact-word models, weak-label calibration,
source/family holdouts, decoded-duplicate grouping, boundary-stability analysis,
swapped-label controls, frozen independent-audit plans, calibration-only source
priors, and a locked gold-evaluation gate.

**Not established:** independently verified native-label accuracy. No human
judgments are invented. Agreement with supplied labels is not proof that those
labels are correct. The validator never changes practice labels, audio files, or recording eligibility
by itself. The separately invoked `build:practice-selection` command now selects
a conservative intersection of supplied-label agreement and the existing
acoustic checks for the user's requested agreement-based practice policy.
This is not a claim that the independent-gold release gate has passed.

## Original labels and hypotheses

```bash
npm run native:inventory -- --output .audit/native-tone-inventory.json
```

The inventory preserves the original stored pinyin, lexical pattern, recorded
surface pattern, source URL, source revision, notes, file SHA-256 and decoded
PCM fingerprint. Alternative dictionary readings must match the exact base
syllables. Dictionary/lexical labels and candidate spoken realizations remain
distinct. Common third-tone/一/不 changes use the existing word-level rules;
ambiguous grouping is flagged rather than declared correct.

Sentence recordings, sentence cuts and stitched audio are excluded. Existing
heuristic approval lists are not training targets. Known quarantines are
excluded from weak-reference fitting but retained in the inventory.

## Label-blind models and source-label evaluation

```bash
npm run native:prepare -- --inventory .audit/native-tone-inventory.json
npm run native:train -- --inventory .audit/native-tone-inventory.json
npm run native:score -- --inventory .audit/native-tone-inventory.json \
  --output .audit/native-tone-predictions.json
```

`native:prepare` uses the existing resumable pitch, unprompted ASR and analysis
alignment collectors. It creates no audio excerpts and never alters practice
approval ledgers. Install `requirements-audio-audit.txt` first. Reusing current
hash-matching evidence avoids unnecessary model reruns.

Each model receives only audio measurements and analysis intervals: absolute
and relative F0, framewise voicing/missingness, contour samples, energy,
durations, positions, and cross-syllable pitch/timing transitions. Neither the
supplied tone, proposed word, source ID, nor pinyin is an acoustic input.

One model predicts all four isolated tones. A separate joint word model predicts
whole two-syllable patterns, including neutral-bearing patterns represented in
the training data. It does not obtain word correctness by averaging independent
syllable pass/fail scores. Three-/four-syllable heads are fitted only when
reference counts support multiple complete patterns; missing coverage stays
explicitly unresolved.

The supplied reading locates a cached alignment; alignment success is not
correctness evidence. Scoring also checks unprompted syllable recognition.
Analysis boundaries are perturbed by +/-20 ms, and the output records whether
the predicted complete pattern survives those changes. Original audio is never
cropped or transformed into new practice recordings.

Training/calibration/test partitions group entire base-syllable or word families,
exact-file duplicates, and identical decoded audio. The entire
`mandarin_native` source is held out as an additional transfer evaluation.
Native word/syllable collections with unknown speakers must **not** be described
as speaker-disjoint. Re-encoded waveforms that are not exactly equal may evade
exact-PCM deduplication; no stronger guarantee is claimed.

Temperature calibration is fitted only on the source-label calibration
partition. It estimates **agreement with weak reference labels**, not the
probability that a recording is independently correct. The evaluation reports
per-pattern confusion, exact-word agreement, token agreement, source-agreement
calibration and swapped-label controls. The controls change proposed labels,
never the acoustic prediction, and are not treated as independent gold errors.

Models are stored in `data/native_tone_validator.json.gz` as non-executable
JSON. `data/native_tone_evaluation.json` binds their evaluation and split
assignments to exact pipeline/configuration/inventory fingerprints.

## Inspect an individual direct recording

```bash
npm run native:score-file -- \
  --audio audio/pinyin_public/zhai3.mp3 \
  --syllables zhai --original-pattern 3 \
  --output .audit/zhai3-native-report.json

npm run native:score-file -- \
  --audio audio/audio_cmn/公司/cmn-公司.mp3 \
  --word 公司 --syllables gong si --original-pattern 1-1 \
  --output .audit/company-native-report.json
```

The result separates label-blind probabilities from label-informed
compatibility. `reference_supported` means provisional acoustic agreement,
not release approval. A high-confidence disagreement becomes `likely_mismatch`;
a known legitimate surface alternative is `possible_spoken_variant`, never a
silent relabeling. Low-confidence, unstable, identity-conflicted or training-
overlapping recordings remain `unresolved`.
If the supplied pattern was absent from the trained classes, the model cannot
call it wrong merely because it predicts some other class. Such cases remain
unresolved, or identify a known spoken alternative without relabeling the clip.

## Independent calibration and locked test

The original labels must not serve as their own test gold. Tone Perfect's
current official `https://tone.lib.msu.edu/cite` page requires a dataset request.
Its bulk corpus has not been obtained or used here.

The following creates a bounded independent-review sample of unseen recordings
from the current library:

```bash
npm run native:audit-plan -- --inventory .audit/native-tone-inventory.json \
  --sampling source --per-source 120 --output .audit/native-independent-labels.json
```

It writes a separate immutable `*-plan.json` plus a pending annotation template.
Byte- or decoded-identical training examples cannot enter the audit, and an
audio file cannot occur in both calibration and test. Speaker independence
still needs external metadata or verified speaker identities; this template
does not manufacture them.

For each item, independent reviewers supply `annotated_pattern` and two or more
attestations:

```json
{
  "reviewer": "<independent reviewer ID>",
  "method": "independent_listening",
  "sha256": "<locked audio hash>",
  "label_identity": "<locked original-label identity>",
  "heard_pattern": "<complete observed pattern, such as 2-3>",
  "identity_correct": true,
  "clear_for_practice": true
}
```

The template contains no fabricated reviews. Original labels are proposals,
not instructions to the listener. In a real audit, listeners should identify
the audio independently before consulting them. Reviewer IDs are attestations,
not cryptographic proof that listening occurred.

Do not drop independently judged bad or unclear recordings from the audit.
Both listeners can mark `identity_correct: false` or `clear_for_practice: false`;
an unclear tone pattern may be `null` only with an explicit unclear judgment.
If the model accepts such an item, the evaluator counts it as a false accept.
Conflicting listener judgments require resolution, not majority-by-default
promotion.

```bash
npm run native:calibrate-gold -- \
  --plan .audit/native-independent-labels-plan.json \
  --labels .audit/native-independent-labels.json \
  --predictions .audit/native-tone-predictions.json \
  --output .audit/native-source-priors.json
```

This command reads **only calibration-role annotations**. Source reliability is
estimated from actual independent correctness counts, with minimum sample
support; no default 95%/99% reliability is assumed. Uniform source sampling
estimates reliability in the frozen unseen-audio pool, not all Mandarin speech.
The optional pattern-stratified `--sampling challenge` plan does not permit
population source priors.

The `native:score` command can consume `--source-priors`. The label-informed
distribution is bounded to 20% prior weight. **A prior cannot change the blind
prediction, rescue weak audio evidence, or override an acoustic contradiction.**
Pending or unsupported prior estimates are not substituted with invented values.

```bash
npm run native:evaluate-gold -- \
  --plan .audit/native-independent-labels-plan.json \
  --labels .audit/native-independent-labels.json \
  --predictions .audit/native-tone-predictions.json \
  --output .audit/native-gold-evaluation.json
```

This checks exact immutable identities, two agreeing reviews, role separation,
no reference-training overlap, complete test coverage and per-source/length/
tone-pattern errors. A zero-observed-error acceptance set must also meet the
configured finite-sample upper bound and minimum counts. Missing reviews or a
small all-correct sample do not pass. The evaluator does not modify the app:
even a passing gate requires a separately reviewed release integration.

## Current release boundary

The native label-blind models and scoring workflows are executable. Training
source-label performance and held-out-source performance are both reported,
without presenting either as independently certified accuracy. The current
three-/four-syllable reference support may be inadequate and is reported as
such rather than forcing a prediction.

Independent native gold and representative calibration remain real prerequisites
for using this validator to certify or broaden production practice. The source
sample and gate workflow make that task bounded and reproducible; they do not
require pretending that an automatic model already achieved 100% accuracy.
