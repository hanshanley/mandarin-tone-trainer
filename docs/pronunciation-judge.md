# Calibrated pronunciation judge

This is a supervised pronunciation-assessment experiment, separate from the
trainer's existing admission rules. Its targets are published human judgments,
not filenames, ASR transcripts, or the app's heuristic decisions.

## Reference data and attribution

The reference is **OMPAL**, pinned in `config/pronunciation_judge.json` and
`data/pronunciation_judge_reference.json`:

> Hsieh, W.-W., Chi, H.-W., Wang, K.-C., Yeh, P.-C., Liu, T.-h., and Chiang, C.-Y.
> (2025). *OMPAL: Bridging Speech and Learning with an Open-Source Mandarin
> Pronunciation Assessment Corpus for Global Learners*. Interspeech 2025,
> 2415-2419. DOI: 10.21437/Interspeech.2025-983.

The corpus is distributed under **CC BY 4.0**. Its original LICENSE and README
are preserved alongside the downloaded audio. The pinned repository contains
1,850 utterances from 49 speakers: 1,768 learner utterances and 82 native
utterances. The source describes French-L1 learners of Mandarin.

The published aggregate binary judgments are used independently for:

- `tone`: whether the annotated pronunciation unit's tone was correct;
- `phoneme_consonant`: whether its consonant pronunciation was correct;
- `phoneme_vowel`: whether its vowel pronunciation was correct.

These are **correctness labels**, not recorded F0 classes. The judge does not
pretend the source supplies a gold transcription of every realized tone. The
individual-rater votes are retained for disagreement-aware reporting. Source
annotation/transcript mismatches are excluded explicitly and included in the
coverage accounting.

Reference recordings are training/evaluation material only. They are not added
to the practice library. Existing sentence cuts are not reintroduced.

## Leakage controls

Complete speakers are assigned deterministically, before acoustic feature
inspection, to five disjoint sets:

| Set | Speakers | Permitted use |
|---|---:|---|
| Train | 25 | Fit classifiers |
| Select | 6 | Select from the predeclared classifier configurations |
| Calibrate | 7 | Fit sigmoid probability calibration |
| Threshold | 5 | Select accept/reject thresholds |
| Test | 6 | Final, untouched evaluation |

The split seed and counts are committed in the configuration. Exact audio hashes
cannot cross partitions. The text prompts may recur across speakers: the result
measures **unseen-speaker performance on this reading protocol**, not unseen
text, all accents, or isolated-word transfer. Pretrained ASR training-corpus
overlap is not known and is not claimed absent.

The feature extractor never receives correctness labels or rater votes. It uses
acoustic measurements, the supplied expected pinyin, and unprompted ASR output.
Forced alignment locates reference units; its success is not considered proof
that those units were pronounced correctly. Traditional text is converted only
for the alignment model, with character-count invariance required.

## Training and calibration

```bash
python3 -m pip install -r requirements-audio-audit.txt
python3 scripts/download_judge_reference.py
python3 scripts/calibrated_pronunciation_judge.py prepare
python3 scripts/calibrated_pronunciation_judge.py train
```

Downloads verify pinned Git blob identities and SHA-256. Measurements are
resumable and bound to the exact audio and feature implementation. Failed
measurements are visible in evaluation coverage, rather than relabeled as
correct or silently omitted from the dataset count.
The actual ASR/alignment checkpoint files and feature-tool package versions
are fingerprinted too; scoring rejects an engine mismatch.

Each target gets a learned tree ensemble. The selection set chooses between the
fixed candidate configurations by log loss. A separate calibration set fits a
monotonic sigmoid to model log-odds. A fourth set chooses thresholds, including
minimum-support requirements and a multiple-threshold-adjusted error bound.
Neither calibration nor threshold selection uses the final test labels.

The saved model is compressed **JSON**, not executable pickle. Its portable
predictions are compared with the training implementation before publication.
The evaluation contains Brier score, log loss, reliability bins/ECE, the
constant-prior baseline, confusion counts, false accepts, false rejects,
abstention coverage, results by expected tone, and a speaker-bootstrap interval.
Units within a speaker are correlated. Unit-level Wilson bounds are therefore
accompanied by speaker-resampled diagnostics; neither is a universal guarantee,
especially with only six held-out speakers. End-to-end coverage includes
annotation or measurement failures as abstentions.

**A well-calibrated constant predictor can still miss every error.** Calibration
and discrimination must be considered together. Automatic acceptance therefore
has a separate held-out false-accept check, not just a high displayed
probability or overall accuracy.

## Assess a recording

```bash
python3 scripts/calibrated_pronunciation_judge.py score \
  --audio audio/audio_cmn/公司/cmn-公司.mp3 \
  --text 公司 --pinyin gong1 si1 \
  --output .audit/company-pronunciation.json
```

Supply numbered pinyin when the intended reading matters; `5` means neutral.
The output contains time-aligned correctness estimates for tone, consonant, and
vowel, along with the unprompted transcription, file hash, and limitations.
The model supports tone categories 1-4 and neutral as reference features; it
does not invent a standalone neutral-tone recording.

## Measured result of this version

The sealed test partition has **six unseen speakers**, 254 annotated utterances,
and 2,906 annotated units. Of those, 2,846 units are measurable; 60 units in four
utterances are explicitly unscorable. The entire reference has 16 excluded
utterances: 14 inconsistent annotation/transcript pairs, one recording outside
the supported duration, and one invalid alignment.

| Human-scored dimension | Calibrated Brier score | Constant-prior Brier | AUROC | Calibration ECE |
|---|---:|---:|---:|---:|
| Tone correctness | 0.0854 | 0.0937 | 0.7420 | 0.0195 |
| Consonant correctness | 0.0185 | 0.0213 | 0.8744 | 0.0070 |
| Vowel correctness | 0.0201 | 0.0217 | 0.8900 | 0.0109 |

Lower Brier/ECE is better; AUROC measures ranking discrimination, not percentage
accuracy. Tone calibration reduces Brier from 0.1554 before calibration to
0.0854 after calibration. It improves probabilities but does not create a
reliable binary pronunciation verdict.

**No head meets the automatic-acceptance gate.** Tone has no threshold with
sufficient evidence for the configured low-error criterion. The provisional
consonant/vowel thresholds miss over 91% of incorrect test units despite
apparently high overall correctness, and therefore fail the error-detection
gate. Their near-zero false-reject counts must not be advertised as success:
they largely reflect accepting rather than detecting errors.

Test support by expected tone is 596 first-tone, 561 second-tone, 509 third-tone,
932 fourth-tone, and 248 neutral-tone units. These are reference tone categories;
correctness labels still come from the humans, not from a pitch heuristic.

Full metrics, reliability bins, speaker uncertainty, support counts and
exclusions are committed in `data/pronunciation_judge_evaluation.json`.
The five split assignments are in `data/pronunciation_judge_splits.json`.

## Scope and deployment gate

Calibration applies to the evaluated population/protocol, not universally.
Performance on French-L1 connected reading cannot certify Mandarin Native,
audio-cmn, arbitrary isolated syllables, microphone noise, or every learner
accent. A matching sentence string does not establish that a recording is
in-domain. Target-corpus transfer remains unvalidated.

This judge is **diagnostic-only** until independent target-domain evaluation
supports production use. It never edits `audio_reviews.json`,
`acoustic_reviews.json`, recording eligibility, or graded answers. Unsupported,
out-of-distribution, or low-evidence cases must remain uncertain.

The CLI always reports a new recording's transfer calibration as unverified,
even when its reference text appeared in the benchmark. Returned numerical
values are **reference-calibrated estimates**, not a verified probability that
an arbitrary recording is correct.

The report is an empirical evaluation, not a guarantee of 100% pronunciation
accuracy. Do not rerun model selection against test results until a preferred
number appears; changes to features/model selection require a newly justified
evaluation protocol.
