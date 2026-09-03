# SPDX-License-Identifier: Apache-2.0
"""Whence -- content-addressed provenance for business figures."""

__version__ = "0.1.0"

# The on-disk formats this build speaks. Bump these, not __version__, when the
# bytes that get hashed change; SPEC.md is written against these strings.
FACT_FORM = "whence.fact.v1"
BUNDLE_PREDICATE = "https://whence.dev/attestation/provenance/v0.1"
