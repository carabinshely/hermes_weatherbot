"""Repository-owned deterministic PAPER experiment factories.

Only reviewed factories under this namespace are callable from the supported PAPER CLI.
Factories return a fully frozen ``PaperExperimentSpec``; they do not publish signals,
change producer eligibility, or access wallet/exchange-write capabilities.
"""
