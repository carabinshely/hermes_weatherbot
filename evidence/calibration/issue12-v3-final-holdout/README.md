# Issue #49 accepted V3 holdout evidence

This directory is the compact reviewed evidence promoted by Issue #50 from the
one-shot Issue #49 holdout run. It intentionally excludes the raw HTTP cache,
cache snapshots, and the multi-megabyte normalized dataset.

- acceptance decision: https://github.com/carabinshely/hermes_weatherbot/issues/49#issuecomment-5426735276
- source workflow run: https://github.com/carabinshely/hermes_weatherbot/actions/runs/32977283488
- source Actions artifact ID: 9610674744
- source Actions artifact ZIP SHA-256: 627fd0f1d420b3ace3bf43522e5e06fc870ba6d538c8948f35caa6b8cf3082ce
- frozen scientific source: fa9f389e21861d66599367160fcac58763b7dec7
- canonical accepted artifact identity: b5c8ad0d90d248459c1253dfa12f5fdb5bfd7e85b9d36ec415eb2a1e63596550
- accepted artifact JSON byte SHA-256: d0690aff605a8a9ca57c2dde260e64a806f60c30e5d3f5662150fae3ab72ae20
- dataset identity: 808ba4c064c96b5df65997bb367ee3d6268ca30386148473932627d4cbb37aca
- manifest identity: 7cab8e59e30a058876d33420b776b327e657c54d4bed3ec1437f03488d1a6c18

The canonical calibration artifact identity is the identity verified by the strict
runtime loader and therefore names the accepted artifact file. The JSON file byte
SHA-256 is recorded separately as transport/file evidence; the two hashes are not
interchangeable.
