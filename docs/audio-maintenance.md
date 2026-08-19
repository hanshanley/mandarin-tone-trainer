# Audio and data maintenance

These workflows update reviewed project data. They are not required for normal
setup.

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

Imported sources are indexed, but native quiz prompts currently select only
verified `audio_cmn` recordings.

## Corpus tools

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
| `audit_native_readings.py` | Resumably screen native words for pinyin mismatches |

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
Mandarin-specific ASR confirmation; it is not automatically removed.

The review policy is conservative:

1. Whisper screens every app-relevant native recording.
2. Mandarin Paraformer independently checks Whisper review candidates.
3. Same-speaker syllable matching resolves disputed initials, finals, and
   polyphonic readings.
4. Only confirmed mismatches receive a replacement or `quiz_eligible: false`;
   model disagreements remain available for human review.

Audit every tone-specific comparison clip with both Praat and pYIN:

```bash
npm run audit:tones
```

This command fails when an app-used clip has a strong contour contradiction
that is not recorded in `data/correction_audio_quality.json`.
The audit uses three independent pitch trackers: pYIN, Praat autocorrelation,
and WORLD/Harvest.

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
