<!-- SPDX-License-Identifier: Apache-2.0 -->

# The Whence Format

**Version 0.1 · fact form `whence.fact.v1` · predicate `https://whence.dev/attestation/provenance/v0.1`**

This document defines the Whence record format, its canonical serialization,
its hash construction, and the `.wbundle` attestation format. It is written so
that an independent verifier can be implemented in any language without reading
the reference implementation.

The spec is the artifact. The Python in this repository is one implementation
of it, and the JavaScript in `whence/report/verify.js` is a second — written
against this document rather than against the Python — which is the closest
thing to a proof that the separation is real.

The key words MUST, MUST NOT, REQUIRED, SHOULD, SHOULD NOT and MAY are to be
interpreted as described in RFC 2119.

---

## 1. What the format is for

A **Fact** is an immutable record of a value together with everything needed to
say where that value came from. Facts reference other Facts by hash, so a set of
Facts forms a Merkle DAG whose root identifier commits to the entire derivation
beneath it.

Two properties follow, and they are the only two that matter:

1. **Agreement without coordination.** Two parties who begin from the same
   inputs and apply the same transforms arrive at the same identifier, having
   never communicated. Equality of identifiers is therefore evidence of
   equality of derivation.
2. **Tamper evidence.** Any change to any part of any Fact changes that Fact's
   identifier, and therefore the identifier of everything derived from it. A
   figure cannot be quietly edited; it can only be replaced by a visibly
   different figure.

Neither property requires trusting the producer, a server, or a signature.

---

## 2. Data model

### 2.1 The Fact

A Fact has exactly six fields.

| Field | Type | Description |
|---|---|---|
| `id` | 64 lowercase hex characters | SHA-256 of the canonical serialization (§3). Derived, never stored as input. |
| `value` | string | The value itself. Always a string — see §3.1. |
| `kind` | `"source"` \| `"derived"` \| `"asserted"` | What sort of claim this is. |
| `transform` | string | The operation that produced it. |
| `inputs` | array of Fact ids | The parents. |
| `metadata` | object, string → string | Everything else the Fact claims. |

### 2.2 Kinds

**`source`** — a value read from a file or system. It MUST have an empty
`inputs` array. Where it was read from is recorded as an *observation* (§5.2),
not in the Fact.

**`derived`** — a value produced by applying `transform` to `inputs`. It MUST
have at least one input. A verifier that knows the transform MUST be able to
recompute the value from the parents' values (§4).

**`asserted`** — a value a human stated, with no computational parent. It MUST
have an empty `inputs` array. An asserted Fact is valid and is recorded like
any other; it is also permanently distinguishable, because `kind` is covered by
the hash. Implementations MUST NOT report an asserted value as verified, and
SHOULD surface it wherever the figures derived from it are displayed.

The distinction is the point. A system that cannot represent "someone typed
this" will have people typing things into it anyway, unlabelled.

### 2.3 Transforms

`transform` names an operation. It is an opaque string as far as this format is
concerned; §4 covers what a verifier does with it. The transforms used by the
reference pipeline are `read`, `assert`, `multiply`, `fx_convert`,
`sum_by_region`, `sum_regions` and `apply_adjustments`.

---

## 3. Canonical serialization and the identifier

### 3.1 Everything hashed is a string

There are **no numbers in a Whence document**. `value` is a string, every
metadata value is a string, and the serialization emits no JSON number tokens
anywhere.

This is deliberate and it is the single most important rule in the format.
JSON numbers have no canonical text form: `1.10`, `1.1` and `1.1e0` are the
same value, IEEE-754 doubles cannot represent most decimal fractions exactly,
and different languages print the same float differently. Any of those would
make identifiers disagree across implementations for reasons that have nothing
to do with the data.

Producers therefore MUST commit to a textual form before hashing. Monetary
values in the reference pipeline are fixed-point with exactly two decimal
places, rounded half-up at the moment the Fact is created — so the rounding is
part of the record and part of the hash, not a display decision made later.

### 3.2 The hashed document

The document that is hashed has exactly these six keys and no others:

```json
{
  "_form":     "whence.fact.v1",
  "inputs":    ["<id>", "..."],
  "kind":      "source | derived | asserted",
  "metadata":  { "<key>": "<value>" },
  "transform": "<name>",
  "value":     "<text>"
}
```

`_form` is a domain separator. It ensures a Whence identifier can never collide
with the hash of some other document that happens to have a similar shape, and
it gives the format a version to break on. Implementations MUST reject a
document whose `_form` they do not recognise.

Note that `id` is **not** part of the hashed document — it is the output.

### 3.3 Serialization rules

The document MUST be serialized as JSON with:

1. **Object keys sorted**, ascending by Unicode code point, at every level.
2. **No insignificant whitespace.** Separator between items is `,` and between
   key and value is `:`, with no spaces.
3. **UTF-8 output.** Non-ASCII characters are emitted literally and MUST NOT be
   `\u`-escaped. String escaping is otherwise standard JSON: `"` and `\` are
   escaped, `\b \f \n \r \t` use their short forms, and other control
   characters use `\u00XX`.
4. **`inputs` sorted** ascending as strings, before serialization (§3.6).
5. **Every leaf a string** (§3.1). A document containing a JSON number,
   boolean, or null is invalid and MUST be rejected.
6. Strings MUST be well-formed UTF-8. Lone surrogates are not permitted.

Metadata keys MUST match `^[a-z][a-z0-9_.]*$` — lowercase ASCII. This
restriction exists so that sorting is unambiguous across languages: JavaScript
sorts strings by UTF-16 code unit and Python by code point, and those orders
agree over ASCII but not in general.

These rules are exactly what `JSON.stringify` produces given pre-sorted keys,
and exactly what Python's
`json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(",", ":"))`
produces. That is not a coincidence; the rules were chosen to be the
intersection of what both do naturally.

### 3.4 The identifier

```
id = lowercase_hex( SHA-256( canonical_bytes ) )
```

### 3.5 Conformance vectors

An implementation is correct on this section when it reproduces all three.

**A. Source Fact — a quantity read from an order export**

```
{"_form":"whence.fact.v1","inputs":[],"kind":"source","metadata":{"key":"src:order:ORD-10001.qty","label":"quantity ordered","order_date":"2026-08-02","order_id":"ORD-10001","region":"EMEA","sku":"SKU-4471"},"transform":"read","value":"20"}
```
```
id = 34010e9b665cf5352e283abdb24c2b9168361c3d06af164abeb6ee0a65de50af
```

**B. Source Fact — a list price with a blank approval reference**

Note that the empty string is serialized, not omitted: a row with no approval
is a different claim from a row with no approval *field*.

```
{"_form":"whence.fact.v1","inputs":[],"kind":"source","metadata":{"approval_ref":"","effective_date":"2026-07-01","key":"src:price:SKU-4471.list_price","label":"SKU-4471 list price","modified_by":"m.jensen","sku":"SKU-4471"},"transform":"read","value":"1200.00"}
```
```
id = b481d59acb2ca60092922532243e6613ef932d0ce240daa862bf37f3d3b4cb5f
```

**C. Derived Fact — the line revenue built from A and B**

```
{"_form":"whence.fact.v1","inputs":["34010e9b665cf5352e283abdb24c2b9168361c3d06af164abeb6ee0a65de50af","b481d59acb2ca60092922532243e6613ef932d0ce240daa862bf37f3d3b4cb5f"],"kind":"derived","metadata":{"currency":"USD","key":"line:ORD-10001","label":"20 x SKU-4471","order_date":"2026-08-02","order_id":"ORD-10001","price_at_sale":"1200.00","price_recognized":"1200.00","region":"EMEA","sku":"SKU-4471"},"transform":"multiply","value":"24000.00"}
```
```
id = 413f6e2ec229a2efa6ee9abb375c18fa5047a1590507897d5d961bfe7146ce13
```

**D. A minimal Fact**, with metadata keys that happen to contain dots — included
because dotted keys sort differently from what a naive implementation might
assume:

```
{"_form":"whence.fact.v1","inputs":[],"kind":"source","metadata":{"key":"src:demo","source.file":"data/x.csv","source.row":"7"},"transform":"read","value":"1234.50"}
```
```
id = dea4dddac67098f0addee32682b8dda025560dc69905cfaa76598be8cd861401
```

These four vectors are parsed directly out of this document by
`tests/test_spec.py` and checked against the implementation, so the spec cannot
drift from the code without a test failing.

### 3.6 Why `inputs` is sorted, and what that costs

Sorting the parents means a Fact's identity does not depend on the order
someone happened to list them in. This is what allows two analysts who iterate
their rows differently — one in file order, one grouped by SKU — to produce
byte-identical identifiers for the same subtotal. It is the property that makes
agreement checkable.

It has one consequence that MUST be handled. For an operation where operand
order carries meaning, sorting would make `a − b` and `b − a` collide. Such
transforms therefore MUST record their operand order in metadata, under the
key **`operands`**: the parent ids in semantic order, joined by `,`. Since
metadata is hashed, the two orderings then produce different identifiers.

A verifier replaying a transform MUST use the order in `operands` when that key
is present, and MAY use any order when it is not (its absence is the producer's
declaration that the operation is commutative).

The reference implementation injects this automatically for every transform not
declared commutative; see `whence/tracked.py`.

### 3.7 What is deliberately not hashed

**Wall-clock time.** No Fact contains a timestamp of when it was computed or
read. If it did, property 1 in §1 would be false: the same computation over the
same inputs would produce a new identifier on every run, and content-addressing
would buy nothing at all. When something happened is a property of a *run*
(§5.3), not of the value.

**Where a value was observed.** A source Fact does not contain the file path,
row number, or file digest it was read from. Those are recorded as observations
(§5.2).

This one is easy to get wrong and expensive to get wrong. If the file digest
were hashed into the Fact, then editing a single row of a 47-row pricebook
would give all 47 prices new identifiers — and a diff between two closes could
no longer distinguish the three prices that changed from the forty-four that
did not. The identifier answers *what is claimed*; the observation log answers
*where we saw it claimed*. Conflating them destroys the more useful question.

Business attributes that describe the claim — a SKU's effective date, who last
modified the row, whether it carries an approval reference — **are** hashed,
because they are part of what the row asserts rather than part of the act of
reading it.

---

## 4. Recomputation

Re-hashing a record proves only that it has not been edited since it was
written. It does not prove that the value follows from its inputs. A conforming
verifier MUST also replay derived Facts.

For each Fact of kind `derived`:

1. Look up `transform` in the implementation's registry of known transforms.
2. If it is unknown, the verifier MUST report the Fact as **not verifiable**.
   It MUST NOT report it as verified, and it MUST NOT silently skip it.
3. Otherwise, collect the parents' `value` fields — in `operands` order if that
   metadata key is present (§3.6), otherwise in any order — apply the
   transform, serialize the result by the producer's stated rules, and compare
   to `value` as a string.

Facts of kind `source` and `asserted` cannot be replayed, having no parents.
A `source` value is checked by re-reading the file it was observed in, when
that file is available; an `asserted` value cannot be checked at all, by
anything, ever, and MUST be reported as such.

Transforms in the reference pipeline:

| Transform | Arity | Commutative | Operation |
|---|---|---|---|
| `multiply` | 2 | yes | product, quantized to 2dp half-up |
| `fx_convert` | 2 | no | amount × rate, quantized to 2dp half-up |
| `sum_by_region` | n | yes | exact sum |
| `sum_regions` | n | yes | exact sum |
| `apply_adjustments` | n | yes | exact sum |

---

## 5. Store layout

This section is **informative**. Nothing here affects an identifier, and an
implementation may store Facts however it likes.

```
.whence/
  objects/ab/cdef….json    one Fact record, filed under its own id
  facts.jsonl              append-only journal, one record per newly-seen Fact
  observations.jsonl       append-only, where values were seen
  runs.jsonl               append-only, one entry per pipeline execution
```

### 5.1 Record form

The stored record is the hashed document plus an `id` field, and keeps
`inputs` in the producer's semantic order rather than sorted. A reader MUST
re-derive the identifier from the record's contents rather than trusting the
`id` field, sorting `inputs` itself as §3.3 requires.

### 5.2 Observations

```json
{"fact": "<id>", "file": "pricebook_v3.csv", "row": "13",
 "column": "list_price", "digest": "<sha256 of the file>"}
```

Append-only, and many per Fact. Two observations of one Fact in two different
files is not a conflict — it means the same claim survived a re-export
unchanged, which during a restatement is exactly what you want to know.

`file` is a display name, not a path. Identity of the bytes comes from
`digest`; storing an absolute path would make two people on two machines
produce different observations for the same read.

`row` is the physical line number in the file, counting the header as line 1,
so that `sed -n '13p'` finds the cited row.

### 5.3 Runs

```json
{"ran_at": "2026-09-30T17:04:11+00:00", "label": "Q3 close",
 "root": "<id>", "gross": "<id>", "orders": 200,
 "sources": {"erp_orders.csv": "<sha256>", "...": "..."}}
```

This is where clocks live (§3.7), and where the binding between a run and the
exact bytes of its input files is recorded.

---

## 6. The `.wbundle` format

A bundle is a single file carrying a figure and the complete set of Facts
needed to verify it, so that a recipient with no access to the producer's
systems can check it.

### 6.1 Reuse of existing standards

Whence does not define a new envelope. Software supply chains solved a
structurally identical problem — *here is an artifact, here is a verifiable
claim about how it came to be* — and the resulting formats fit without
modification:

- the envelope is a [DSSE](https://github.com/secure-systems-lab/dsse) envelope;
- the payload is an
  [in-toto Attestation Framework](https://github.com/in-toto/attestation)
  Statement v1;
- only the *predicate* is Whence-specific.

Adopting these means a bundle can be handled, stored and transported by tooling
that has never heard of Whence, and that the parts most likely to be got wrong
— envelope parsing, signature binding — are borrowed from specs that have had
far more scrutiny than this one.

The in-toto specification explicitly permits a subject digest that identifies a
structured metadata object rather than a file's bytes, provided the
canonicalization is documented. §3 is that documentation.

### 6.2 Envelope

```json
{
  "payloadType": "application/vnd.in-toto+json",
  "payload": "<base64 of the Statement>",
  "signatures": [{"keyid": "", "sig": ""}]
}
```

`payload` is base64 of the UTF-8 Statement bytes. DSSE permits standard or
URL-safe base64 and a verifier MUST accept either.

DSSE requires `signatures` to contain at least one entry, with `sig` present
even when empty. **Bundles produced by this implementation are unsigned**, and
say so with a single empty signature. A verifier MUST treat an empty `sig` as
"the origin of this bundle is unattested" — the hashes still establish what the
bundle says and that it has not been altered, but nothing establishes who
produced it. An envelope with an empty `signatures` array is malformed and MUST
be rejected.

Signing is not implemented because key custody is a real problem this proof of
concept does not solve, and a hardcoded key would be theatre. The bytes a
signer must sign are nonetheless fully determined, by the DSSE
Pre-Authentication Encoding:

```
PAE(type, body) = "DSSEv1" SP LEN(type) SP type SP LEN(body) SP body
```

where `SP` is one 0x20 byte and `LEN` is the ASCII decimal **byte** length with
no leading zeros. The raw Statement bytes are signed, never the base64. This is
implemented and tested as `whence.attest.pae` against the vector in the DSSE
specification.

### 6.3 Statement

```json
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject": [{"name": "Q3 Net Revenue",
               "digest": {"sha256": "<root fact id>"}}],
  "predicateType": "https://whence.dev/attestation/provenance/v0.1",
  "predicate": { ... }
}
```

The subject digest is the root Fact's identifier — which, by §3.4, already *is*
the SHA-256 of that Fact's canonical serialization. The in-toto convention
therefore holds exactly: the artifact is a record, named by the hash of its own
content.

### 6.4 Predicate

```json
{
  "whenceVersion": "0.1.0",
  "factForm": "whence.fact.v1",
  "root": "<id>",
  "value": "<the figure, as text>",
  "facts": [ <every record in the closure, ascending by id> ],
  "observations": [ <§5.2 entries for those facts> ],
  "runs": [ <§5.3 entries that produced them> ]
}
```

`facts` MUST contain the **complete transitive closure** of `root`: every Fact
reachable by following `inputs`. A bundle missing a parent is not a smaller
proof, it is not a proof.

Ordering by id makes export deterministic: two people bundling the same figure
produce byte-identical files.

### 6.5 Verification algorithm

A conforming verifier, given a bundle and nothing else, MUST perform all of the
following and MUST report failure if any step fails.

1. Parse the envelope. Reject an unexpected `payloadType`, or an empty
   `signatures` array. Decode `payload`.
2. Reject an unexpected `_type` or `predicateType`, or a `factForm` the
   implementation does not speak.
3. **Integrity.** For every record in `facts`, re-derive the identifier from
   its own contents per §3 and compare with the id it is filed under. Any
   mismatch is a failure.
4. **Completeness.** For every Fact, every id in `inputs` MUST be present in
   `facts`. A gap is a failure, reported against the Fact that depends on it.
5. **Soundness.** Replay every `derived` Fact per §4.
6. **Binding.** The subject digest MUST equal `predicate.root`, and
   `predicate.value` MUST equal the root Fact's `value`.
7. **Disclosure.** Report every `asserted` Fact. These are never counted as
   verified.
8. Report the unsigned status (§6.2) rather than implying provenance of origin.

Having verified, an implementation MUST NOT re-parse the envelope to obtain the
payload again; the bytes that were verified are the bytes to use. DSSE is
explicit about this, and it is the reason `whence verify-bundle` carries the
input-file digests out of the parsed statement rather than reopening the file
to print them.

---

## 7. Conformance checklist

An independent implementation is conforming when it:

- [ ] reproduces the four vectors in §3.5;
- [ ] rejects any document containing a JSON number, boolean or null;
- [ ] rejects metadata keys outside `^[a-z][a-z0-9_.]*$`;
- [ ] sorts `inputs` before hashing, and honours `operands` when replaying;
- [ ] rejects a `source` or `asserted` Fact with inputs, and a `derived` Fact
      without them;
- [ ] never reports an `asserted` Fact as verified;
- [ ] never reports an unknown transform as verified;
- [ ] implements all eight steps of §6.5;
- [ ] computes `PAE` as in §6.2.

`whence/report/verify.js` is a worked example: a complete independent
implementation of §3 and a partial one of §6.5, in about 150 lines, embedded in
every generated report so that the page re-derives its own figures in the
reader's browser.

---

## 8. Versioning

`_form` (`whence.fact.v1`) covers the hashed document. Any change to §3 that
would alter an identifier is a breaking change and MUST come with a new
`_form` string. Adding a metadata key to a *particular* Fact is not such a
change — metadata is open, and a new key simply produces a different Fact.

`predicateType` carries the bundle version. Per the in-toto convention, the URI
reflects the major version, and `0.x` versions are each treated as major.

Section 5 is informative and may change freely.
