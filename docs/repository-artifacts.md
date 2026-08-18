# Repository artifact contract

Issue #24 defines which Hermes artifacts belong in Git and which are local/generated state.
The rule is intentionally simple: source and reviewed deterministic evidence are committed;
mutable runtime/working data are not.

## Artifact classes

| Artifact | Canonical location | Git policy |
| --- | --- | --- |
| source, configuration, tests, scripts | repository source paths | committed |
| synthetic/redacted deterministic test inputs | `tests/fixtures/**` | committed |
| producer signal log | `state/signals-v1.jsonl` | ignored |
| PIP outbox/dead-letter/runtime database | `state/**` | ignored |
| PAPER ledger/archive | `state/**` | ignored |
| normal PAPER experiment results | `state/paper-experiments/**` | ignored |
| calibration raw HTTP cache | `data/calibration/**` | ignored |
| calibration working datasets/reports | `data/calibration/**` | ignored |
| legacy market/runtime/learning data | `data/**` | ignored |
| promoted PAPER development evidence | `evidence/paper/**` | explicitly reviewed and committed |
| accepted calibration evidence | `evidence/calibration/**` | explicitly reviewed and committed by the acceptance workflow |

`data/` and `state/` are local working namespaces even when historical compatibility code
continues to read or write them. Removing tracked generated files does not remove the legacy
code paths themselves.

## PAPER evidence promotion

Normal internal PAPER experiments write canonical results below an ignored output root such
as:

```text
state/paper-experiments/<paper_exp_id>/
├── summary.json
├── evaluations.jsonl
└── checksums.json
```

A result enters Git only after an explicit human promotion decision. A promoted bundle must
live at `evidence/paper/<paper_exp_id>/` and contain at least:

```text
manifest.json
provenance.json
summary.json
checksums.json
```

`evaluations.jsonl` is optional and should be committed only when it is compact and useful
for review.

`provenance.json` must identify the experiment, strategy ID/version, producer-policy
fingerprint, source commit, manifest digest, regeneration command, and state that the bundle
is development evidence only. Never promote SQLite ledgers, archives, signing keys, wallet
material, logs, caches, temporary files, or unbounded raw datasets.

## Calibration evidence

Calibration collection and fitting use `data/calibration/**` as local working storage. Raw
provider responses, immutable HTTP cache entries, normalized working datasets, and routine
fit outputs remain outside Git.

The final V3 acceptance workflow (#49/#50) owns the decision to promote a small reviewed
artifact set into `evidence/calibration/**`. Creating that namespace does not authorize a
rejected or development-only calibration artifact for runtime use.

## Regeneration lifecycle

```text
generate locally
      ↓
ignored working artifact
      ↓
reproduce / checksum / inspect
      ↓
explicit promotion decision
      ↓
small evidence bundle
      ↓
Git review
```

A supported producer or PAPER run should not dirty the working tree unless evidence
promotion was explicitly requested.

## Enforcement

`tests/test_repository_safety.py` enforces the boundary with Git itself:

- forbidden runtime/generated paths must not be tracked;
- representative generated paths must match `.gitignore`;
- `tests/fixtures/**` and `evidence/**` must remain committable;
- any committed PAPER evidence bundle must satisfy its minimum structure and checksum
  contract;
- fixture/evidence trees must not contain obvious private-key or local-environment files.

The repository's existing secret scanning remains authoritative for content-level secret
detection.
