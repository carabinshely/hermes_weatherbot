# Reviewed development evidence

This tree is the explicit exception to Hermes' generated-artifact ignore policy.

Only small, deterministic, reviewed evidence bundles belong here. Raw caches, working
datasets, runtime databases, logs, dashboards, temporary packages, producer state, PIP
outbox/dead-letter state, and mutable PAPER state belong under ignored `data/` or `state/`
namespaces instead.

See `docs/repository-artifacts.md` before promoting any generated artifact into Git.
