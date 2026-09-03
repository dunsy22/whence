# SPDX-License-Identifier: Apache-2.0
"""The board page.

Two things matter here and neither is cosmetic: the file must be genuinely
self-contained (a page that phones home is not a document you can hand to an
auditor), and the numbers printed on it must be bound to facts that are
actually embedded in it.
"""

import json
import re
from urllib.parse import urlsplit

from whence import BUNDLE_PREDICATE
from whence.report import render_report

#: Absolute URLs the page is allowed to contain *as text*. The predicate host
#: is derived from the constant rather than written out, so moving the
#: attestation namespace to a different domain does not silently need a test
#: edited to match. The others are standards and licence references.
ALLOWED_URL_HOSTS = {
    urlsplit(BUNDLE_PREDICATE).netloc,
    "in-toto.io",
    "www.apache.org",
    "apache.org",
    "www.w3.org",
}


def page(store, root):
    return render_report(store, root)


def test_the_page_renders(store, first):
    html = page(store, first.net.id)
    assert html.startswith("<!doctype html>")
    assert "Q3 Net Revenue" in html
    assert "$4,8" in html or "$5,2" in html


def test_the_page_has_no_external_references(store, first):
    """No CDN, no font service, no analytics, no anything. Inline or absent."""
    html = page(store, first.net.id)
    external = re.findall(r'(?:src|href)\s*=\s*["\'](?!#)([^"\']+)', html)
    assert external == [], "page reaches out to: %r" % external
    # The only absolute URLs allowed are type identifiers and standards
    # references printed as text, never as something the page would fetch.
    for hit in re.findall(r"https?://\S+", html):
        host = urlsplit(hit.rstrip('",;)')).netloc
        assert host in ALLOWED_URL_HOSTS, "unexpected URL on the page: %s" % hit


def test_the_css_and_js_are_inlined_not_linked(store, first):
    html = page(store, first.net.id)
    assert "<style>" in html and "</style>" in html
    assert "WhenceVerify" in html
    assert "sha256" in html


def test_every_marked_figure_is_backed_by_an_embedded_fact(store, first):
    html = page(store, first.net.id)
    data = json.loads(
        re.search(r'id="whence-data" type="application/json">(.*?)</script>',
                  html, re.S).group(1))
    marked = set(re.findall(r'data-fact="([0-9a-f]{64})"', html))
    assert marked, "the page should mark its figures"
    for fid in marked:
        assert fid in data["facts"], "%s is shown but not embedded" % fid


def test_the_embedded_facts_are_the_whole_closure(store, first):
    html = page(store, first.net.id)
    data = json.loads(
        re.search(r'id="whence-data" type="application/json">(.*?)</script>',
                  html, re.S).group(1))
    assert set(data["facts"]) == set(store.closure(first.net.id))


def test_asserted_figures_are_flagged_in_the_markup(store, first):
    html = page(store, first.net.id)
    assert html.count("ASSERTED") >= len(first.adjustments)
    assert "cannot be recomputed" in html


def test_the_findings_appear_on_the_page(store, first):
    html = page(store, first.net.id)
    assert "no approval reference" in html
    assert "SKU-4471" in html


def test_the_page_reports_the_source_digests(store, first):
    html = page(store, first.net.id)
    for digest in first.sources.values():
        assert digest[:32] in html
