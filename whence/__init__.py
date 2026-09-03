# SPDX-License-Identifier: Apache-2.0
"""Whence -- content-addressed provenance for business figures."""

__version__ = "0.1.0"

# The on-disk formats this build speaks. Bump these, not __version__, when the
# bytes that get hashed change; SPEC.md is written against these strings.
FACT_FORM = "whence.fact.v1"

# The in-toto predicate type carried by every .wbundle.
#
# in-toto does not register type URIs -- it relies on the natural namespacing
# of URIs to keep them from colliding -- so this has to sit somewhere its
# authors actually control. A path under a GitHub account is exactly that, and
# costs nothing to hold indefinitely. It does not currently resolve, which the
# in-toto specification permits (a TypeURI SHOULD resolve to a human-readable
# description but MAY be unresolvable).
#
# This string is a stable format identifier. Changing it is a format break for
# anyone holding a bundle, so it changes only with the major version in its
# path -- see SPEC.md section 8. Note that it is NOT part of any fact's hash;
# only FACT_FORM above is.
BUNDLE_PREDICATE = "https://github.com/dunsy22/whence/attestation/provenance/v0.1"
