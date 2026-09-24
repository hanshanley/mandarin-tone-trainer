# Tone-label validation redesign

**Status: implementation available; independent native-gold validation is pending.**
See [the executable native validator workflow](native-tone-validator.md).
Original-label hypotheses, label-blind single/pair models, duplicate/family/source
holdouts, calibration and independent audit gates are implemented. Reference
support is insufficient for validated longer-word heads, and no production
accuracy guarantee or automatic admission has been enabled.
The currently failed learner-pronunciation judge must not become the authority
for the native practice library merely by relaxing its thresholds.

## Objective and limits

Validate whether each intact recording is a clear example of its proposed
spoken-tone pattern. Keep direct character/syllable and whole-word recordings;
do not introduce sentence cuts or concatenate syllables.

The release target is zero observed wrong tone labels in a locked, independently
adjudicated validation set, with no unresolved contradictions among admitted
examples. Report the size, coverage, source/speaker composition, and statistical
error bound. Zero observed errors is not proof of universal 100% accuracy.
An automatic confidence score of 1.0 is not an accuracy certificate.

Separate these tasks:

- **Library tone-label verification:** what tones are realized in an existing
  native recording, and is it unambiguous enough to teach?
- **Learner pronunciation assessment:** how a learner's production compares
  with the target reading.
- **Playback integrity:** whether the UI plays the exact assessed audio and
  shows the corresponding answer.

OMPAL was built for the second task. Retain it as supplementary error evidence;
do not use its correctness scores to claim native-library calibration.

## Research implications

1. Single-syllable recognition is not equivalent to spotting one fixed curve.
   Use normalized pitch height, contour direction/curvature and turn timing,
   with duration and voicing quality. A low/creaky third tone is not necessarily
   a clean dip-and-rise trace. A nearly flat contour alone does not establish
   first tone: register matters.
2. For two-syllable words, evaluate the intact pair. Pitch, relative duration,
   articulation timing, stress and tonal context all matter. Rhythm alone
   cannot determine lexical tones.
3. Treat lexical tones and spoken realizations separately. Third-tone sandhi,
   the changes of 一/不, and neutral realizations can legitimately change the
   observed pattern. Avoid teaching a lexical 3-3 sequence as an acoustic 3-3
   sequence when the recording realizes 2-3.
4. Neutral tone needs its surrounding word, lexical information and prosodic
   evidence. Do not invent an isolated neutral-tone exemplar or require one
   universal neutral pitch height.
5. Pitch trackers share the same signal and can share errors. Their agreement
   is useful evidence, not independent listener confirmation.

## Original labels: useful but uncertain

Preserve the original labels and provenance unchanged. Build a separate set of
allowed reading/spoken-tone hypotheses from the original annotation, a
reading-specific dictionary, and explicitly modeled context.

Produce two evaluations for every clip:

- **Label-blind acoustic prediction:** recognize the tone or sequence without
  showing the model the proposed answer.
- **Label-informed compatibility:** assess the proposed label and legitimate
  surface alternatives, including a source-reliability prior.

Estimate source priors from an independent, representative audit; do not assume
an arbitrary 95% or 99% reliability. Compare against a label-only baseline.
The label-informed route must not override a strong label-blind contradiction.
Cases where confidence comes almost entirely from the supplied label stay
unresolved. Original labels may supervise candidate training with
noise-aware weighting, but cannot also serve as the independent test gold.

Test this directly by permuting proposed labels while keeping the audio
unchanged. The judge must detect tone substitutions, not repeat metadata.
Label-swapped controls are useful controls, not a substitute for real errors or
natural variants in calibration.

## Model design

### A. Direct single-syllable recordings

First build a native 1/2/3/4 tone recognizer, not another general binary
"pronunciation correct" classifier. Inputs include unmodified audio features,
time-resolved F0 with uncertainty, voicing/creak evidence and speaker-normalized
register. Keep an interpretable phonetic comparator alongside the learned model.

Evaluate all four candidate tones for the same base syllable. Compare against
independent direct recordings of that syllable where available; agreement
within one source cannot certify that source's labels. Match source hashes to
prevent duplicate or re-encoded copies crossing validation partitions.

Do not insist that every base syllable has a standalone neutral sample.

### B. Direct two-syllable words

Train/evaluate a context-sensitive sequence model on intact native word audio.
Use temporal representations, F0 trajectories, inter-syllable transitions,
duration ratio, stress/reduction and boundary uncertainty. Preserve actual time
coordinates rather than normalizing each syllable independently until rhythm
and transitions disappear.

Assess plausible tone-pair hypotheses jointly. Compare the joint result with
the per-syllable result and uncertainty. Boundary sensitivity tests must perturb
analysis boundaries within plausible alignment error; unstable decisions do not
pass. Analysis windows are not exported as new practice recordings.

Report coverage by valid lexical pair, realized pair and neutral context.
Do not confuse a missing spoken 3-3 pair with a dataset defect when sandhi
accounts for its realization.

### C. Longer words

Extend the sequence model only after single-syllable and pair validation passes.
Keep authentic whole-word recordings. Score the complete spoken pattern, not
an average that hides one wrong syllable. Report both token-level and exact
whole-word accuracy, by length and source.

## Independent reference data

Use three distinct resources:

1. Independently labeled native references, matched to isolated syllables and
   direct words. Tone Perfect is a strong isolated-tone candidate: its authors
   describe four-tone recordings from six native speakers. Their published
   access process restricts bulk data to approved non-commercial projects.
   Verify current terms and obtain appropriate access before using it; do not
   assume it can be bundled with the app. It does not supply a complete
   disyllabic/neutral benchmark.
2. A small, independently adjudicated gold subset of this app's actual direct
   recordings. Stratify by source, available speaker identity, tone, tone pair,
   neutral context and word length. Include ordinary accepted items, suspicious
   items and known defects. Do not require the user to listen to every track.
3. A separate hard-negative challenge set: real mislabeled recordings,
   independently established tone confusions, homographs, natural valid
   variants, poor F0 tracking and invalid-input controls.

Use a representative audit to estimate deployment error/prevalence. An
error-enriched challenge set measures detection of mistakes but cannot directly
calibrate deployment probabilities without sampling/prevalence adjustment.

Speakers/sessions and duplicate audio must not cross train/calibration/test
partitions. Hold out base-syllable or word families as an additional test and
hold out an entire source for transfer testing. Where speaker identity is
unknown, report that limitation rather than claim speaker-disjoint validation.
The already examined OMPAL benchmark is not a new locked test.

## Decision policy

Use explicit outcomes:

- **Supported:** source label or legitimate spoken variant is supported by
  acoustic evidence and the validated decision rule.
- **Likely mismatch:** strong contradictory evidence; exclude pending resolution,
  retaining the original annotation and proposed correction separately.
- **Unresolved:** ambiguous contour, bad signal, uncertain boundary, unknown
  reading, domain mismatch or insufficient reference support.

Unresolved does not mean wrong. Failing a hand-written pitch template does not
by itself justify removing or relabeling an otherwise credible native clip.
Do not tune the threshold until a desired library count appears.

Choose class/source/context-specific thresholds on calibration data, not the
test set. Publish false accepts, false rejects, coverage, confusion matrices,
calibration plots and confidence intervals per tone and whole word. Recheck
both accepted-decision contamination and the share of actual errors missed.
Do not call an all-abstaining model successful.

## Implementation order and completion criteria

| Phase | Deliverable | Required evidence before moving on |
|---|---|---|
| 1. Label/provenance audit | Original-label, dictionary-reading and permitted-surface hypothesis manifest | No silent relabels; direct audio only; duplicates grouped |
| 2. Native reference and gold set | Pinned sources, terms, speaker groups and independent judgments | Suitable reference population; test labels independent from weak source labels |
| 3. Single-syllable validator | Label-blind four-tone model plus label-informed compatibility | Per-tone confusion/false-accept results, including third-tone variants and swapped-label controls |
| 4. Pair validator | Intact-word sequence model with timing and contextual tone handling | Exact pair results across valid patterns; boundary stability; neutral-context validation |
| 5. Longer-word validator | Same sequence framework on whole words | Exact-word errors and uncertainty by length; no averaging away a wrong syllable |
| 6. Release gate | Versioned supported-recording manifest | Zero observed errors in the locked acceptance evaluation, stated finite-sample bound, unresolved contradictions excluded |
| 7. Playback regression | Initial audio and each tone/neutral button bound to assessed bytes | Correct files, labels, navigation, voice switching and packaged assets |

Phase 2 is a real dependency, not something that more tuning of a
learner-assessment benchmark can replace. Do not promote the existing prototype
or broaden the current practice pool while this plan is still unvalidated.

## Research consulted

- Kang and Xu (2024), *Tone-syllable synchrony in Mandarin: New evidence and
  implications*. Speech Communication 163, 103121.
  DOI: 10.1016/j.specom.2024.103121.
- Xu, *Research Overview*, University College London: contextual tonal
  variation, target approximation, syllable timing and neutral-tone behavior.
- Huang, Hu and Xu (2017), *Mandarin tone modeling using recurrent neural
  networks*. arXiv:1711.01946. Contextual tone embeddings and duration are
  modeled alongside sequential acoustic information.
- *A Tone Perfect Story: How to Develop an Open Access Mandarin Chinese Audio
  Database as a Collaborative Digital Humanities Project*, project-author
  account in IDEAH 2(1). Describes corpus construction, speaker coverage and
  the non-commercial bulk-access process.
- Hsieh et al. (2025), *OMPAL*, Interspeech 2025, 2415-2419.
  DOI: 10.21437/Interspeech.2025-983. A non-native pronunciation-assessment
  corpus, not an isolated-native-tone certification set.
- Chen and Xu (2006), *Production of Weak Elements in Speech: Evidence from
  F0 Patterns of Neutral Tone in Standard Chinese*. Phonetica 63, 47-75.
  DOI: 10.1159/000091406.
- Gangrade, Kag and Saligrama (2021), *Selective Classification via One-Sided
  Prediction*, AISTATS/PMLR 130, 2179-2187. Explicit accuracy/coverage trade-off
  and class-wise low-false-positive decision sets.
- NIST/SEMATECH, *Exact Binomial Confidence Limits*. Finite zero-error
  observations still require a confidence bound.
