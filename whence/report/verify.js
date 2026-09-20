// SPDX-License-Identifier: Apache-2.0
//
// A second, independent implementation of the Whence verifier -- about 150
// lines of it, running in the reader's browser, inlined into this page.
//
// The point of shipping it is not convenience. If verification only ever
// happens inside the program that produced the number, "verified" means
// nothing more than "the producer says so". This file re-derives every id on
// the page from the fact records embedded below it, using nothing from Python,
// and re-adds every subtotal in integer cents. If the Python and the
// JavaScript disagree, the strip at the top of the page turns red.
//
// It implements the rules in SPEC.md section 3 and nothing else:
//   canonical JSON, keys sorted, no whitespace, every leaf a string,
//   inputs sorted, SHA-256 over the UTF-8 bytes.

(function () {
  "use strict";

  // ---- SHA-256 ----------------------------------------------------------
  // Written out in full rather than calling crypto.subtle, for two reasons:
  // it is synchronous, and a reader can check it against the spec without
  // trusting the host. It self-tests against a published vector before the
  // page will report anything as verified.

  var K = [
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
  ];

  function sha256(bytes) {
    var h = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,
             0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
    var bitLen = bytes.length * 8;
    var padded = bytes.length + 9;
    var total = padded + ((64 - (padded % 64)) % 64);
    var m = new Uint8Array(total);
    m.set(bytes);
    m[bytes.length] = 0x80;
    // Length as a 64-bit big-endian count of bits. Facts are small; the high
    // word is written anyway so the implementation is not subtly wrong.
    var hi = Math.floor(bitLen / 0x100000000);
    var lo = bitLen >>> 0;
    m[total - 8] = (hi >>> 24) & 0xff; m[total - 7] = (hi >>> 16) & 0xff;
    m[total - 6] = (hi >>> 8) & 0xff;  m[total - 5] = hi & 0xff;
    m[total - 4] = (lo >>> 24) & 0xff; m[total - 3] = (lo >>> 16) & 0xff;
    m[total - 2] = (lo >>> 8) & 0xff;  m[total - 1] = lo & 0xff;

    var w = new Int32Array(64);
    for (var off = 0; off < total; off += 64) {
      for (var i = 0; i < 16; i++) {
        w[i] = (m[off+i*4] << 24) | (m[off+i*4+1] << 16) |
               (m[off+i*4+2] << 8) | m[off+i*4+3];
      }
      for (i = 16; i < 64; i++) {
        var g0 = w[i-15], g1 = w[i-2];
        var s0 = (rotr(g0,7) ^ rotr(g0,18) ^ (g0 >>> 3));
        var s1 = (rotr(g1,17) ^ rotr(g1,19) ^ (g1 >>> 10));
        w[i] = (w[i-16] + s0 + w[i-7] + s1) | 0;
      }
      var a=h[0],b=h[1],c=h[2],d=h[3],e=h[4],f=h[5],g=h[6],hh=h[7];
      for (i = 0; i < 64; i++) {
        var S1 = rotr(e,6) ^ rotr(e,11) ^ rotr(e,25);
        var ch = (e & f) ^ (~e & g);
        var t1 = (hh + S1 + ch + K[i] + w[i]) | 0;
        var S0 = rotr(a,2) ^ rotr(a,13) ^ rotr(a,22);
        var maj = (a & b) ^ (a & c) ^ (b & c);
        var t2 = (S0 + maj) | 0;
        hh=g; g=f; f=e; e=(d + t1)|0; d=c; c=b; b=a; a=(t1 + t2)|0;
      }
      h[0]=(h[0]+a)|0; h[1]=(h[1]+b)|0; h[2]=(h[2]+c)|0; h[3]=(h[3]+d)|0;
      h[4]=(h[4]+e)|0; h[5]=(h[5]+f)|0; h[6]=(h[6]+g)|0; h[7]=(h[7]+hh)|0;
    }
    var out = "";
    for (var j = 0; j < 8; j++) out += ((h[j] >>> 0).toString(16)).padStart(8, "0");
    return out;
  }

  function rotr(x, n) { return (x >>> n) | (x << (32 - n)); }

  function utf8(str) {
    if (typeof TextEncoder !== "undefined") return new TextEncoder().encode(str);
    var esc = unescape(encodeURIComponent(str));
    var out = new Uint8Array(esc.length);
    for (var i = 0; i < esc.length; i++) out[i] = esc.charCodeAt(i);
    return out;
  }

  // ---- canonical serialization (SPEC.md section 3) ----------------------
  //
  // JSON.stringify already matches Python's json.dumps for the subset Whence
  // permits: every leaf is a string, the escaping rules agree, and non-ASCII
  // is emitted literally by both. The only thing it does not do is sort keys,
  // so we do that here. Whence restricts metadata keys to lowercase ASCII,
  // which is why a plain .sort() is enough -- JavaScript sorts by UTF-16 code
  // unit and Python by code point, and those agree over ASCII.

  function stable(v) {
    if (v === null || typeof v !== "object") return JSON.stringify(v);
    if (Array.isArray(v)) return "[" + v.map(stable).join(",") + "]";
    var keys = Object.keys(v).sort();
    return "{" + keys.map(function (k) {
      return JSON.stringify(k) + ":" + stable(v[k]);
    }).join(",") + "}";
  }

  function canonical(fact) {
    return stable({
      _form: "whence.fact.v1",
      inputs: (fact.inputs || []).slice().sort(),
      kind: fact.kind,
      metadata: fact.metadata || {},
      transform: fact.transform,
      value: fact.value
    });
  }

  function factId(fact) { return sha256(utf8(canonical(fact))); }

  // ---- decimal money as integer cents -----------------------------------

  function cents(text) {
    var neg = text.charAt(0) === "-";
    var t = neg ? text.slice(1) : text;
    var dot = t.indexOf(".");
    var whole = dot < 0 ? t : t.slice(0, dot);
    var frac = dot < 0 ? "" : t.slice(dot + 1);
    frac = (frac + "00").slice(0, 2);
    var v = BigInt(whole || "0") * 100n + BigInt(frac);
    return neg ? -v : v;
  }

  function fromCents(v) {
    if (v === 0n) return "0.00";               // never a signed zero
    var neg = v < 0n, a = neg ? -v : v;
    var s = a.toString().padStart(3, "0");
    return (neg ? "-" : "") + s.slice(0, -2) + "." + s.slice(-2);
  }

  var ADDITIVE = { sum_by_region: 1, sum_regions: 1, apply_adjustments: 1 };

  // A `quantize` key means the producer used a scale other than cents; its
  // absence is the declaration that cents were used (SPEC.md section 3.7).
  function declaresScale(fact) {
    return !!(fact && fact.metadata && fact.metadata.quantize);
  }

  // ---- the check --------------------------------------------------------

  function verifyAll(facts) {
    var result = {
      checked: 0, hashOk: 0, sumsOk: 0, asserted: 0, sources: 0,
      notRecomputable: 0, failures: []
    };

    // Self-test first. If the hash function in this file is wrong, every
    // other answer it gives is worthless.
    if (sha256(utf8("abc")) !==
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad") {
      result.failures.push("the SHA-256 implementation on this page failed " +
                           "its own test vector");
      return result;
    }

    Object.keys(facts).forEach(function (id) {
      var fact = facts[id];
      result.checked++;

      if (factId(fact) !== id) {
        result.failures.push(id.slice(0, 12) + ": contents do not hash to " +
                             "this id -- the record has been altered");
        return;
      }
      result.hashOk++;

      var missing = (fact.inputs || []).filter(function (i) { return !facts[i]; });
      if (missing.length) {
        result.failures.push(id.slice(0, 12) + ": " + missing.length +
                             " parent fact(s) missing from this page");
        return;
      }

      if (fact.kind === "asserted") { result.asserted++; return; }
      if (fact.kind === "source")   { result.sources++;  return; }

      // Everything below counts in integer cents, so a fact that declares a
      // different scale -- or that draws on one -- is outside what this page
      // can check. `whence verify` handles any scale; saying so is better
      // than adding two-decimal numbers that were never two-decimal.
      if (declaresScale(fact) ||
          (fact.inputs || []).some(function (i) { return declaresScale(facts[i]); })) {
        result.notRecomputable++;
        return;
      }

      if (ADDITIVE[fact.transform]) {
        var total = 0n;
        fact.inputs.forEach(function (i) { total += cents(facts[i].value); });
        if (fromCents(total) !== fact.value) {
          result.failures.push(id.slice(0, 12) + " (" +
            (fact.metadata.label || fact.transform) + "): stored " +
            fact.value + " but its inputs add up to " + fromCents(total));
          return;
        }
        result.sumsOk++;
      } else {
        // Multiplications and FX conversions are bound by the hash but are
        // not re-run here: doing decimal multiplication with the same
        // rounding rules in two languages is exactly the kind of thing that
        // silently disagrees. `whence verify` re-runs them properly. Saying
        // so is better than implying this page checked more than it did.
        result.notRecomputable++;
      }
    });

    return result;
  }

  window.WhenceVerify = {
    run: verifyAll, factId: factId, canonical: canonical,
    cents: cents, fromCents: fromCents, sha256: sha256, utf8: utf8
  };
})();
