# Promoted calibration evidence

Calibration working data belongs under ignored `data/calibration/**`.

This namespace is reserved for the small reviewed artifact set explicitly selected by the
V3 acceptance workflow (#49/#50). Raw HTTP cache entries and ordinary development/replay
outputs must never be promoted merely because they are reproducible.

Any promoted bundle must retain the artifact/data identities and checksums required to
reproduce and audit the acceptance decision without committing the large raw cache.
