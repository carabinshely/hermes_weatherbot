# Dependency profile completion summary

This change completes the remaining scope of issue #21:

- research and paper use the minimal locked runtime;
- wallet, signing, Web3, and official SDK packages are isolated in the `live` extra;
- optional modules are imported only after an explicit live dependency check;
- safe commands run without live packages;
- live commands fail with an actionable installation command when the extra is absent;
- continuous integration validates both profiles;
- security CI audits minimal and live dependency exports separately.
