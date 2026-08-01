"""Parser tests, built around the data quirks actually met in the corpus."""
from __future__ import annotations

import pytest

from journal2epub.model import (
    DisplayMath, Emphasis, ExternalLink, Figure, InlineMath, InternalLink,
    ListBlock, Paragraph, Section, Table, all_blocks, inline_text, walk_blocks,
    walk_inlines,
)
from journal2epub.sources.jats import JatsParser, _clean_tex, _strip_artifacts

ARTICLE = """<?xml version="1.0"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink"
         xmlns:ali="http://www.niso.org/schemas/ali/1.0/"
         xmlns:mml="http://www.w3.org/1998/Math/MathML"
         article-type="research-article" dtd-version="1.4">
<front>
  <journal-meta>
    <journal-id journal-id-type="nlm-ta">Gigascience</journal-id>
    <journal-title-group><journal-title>GigaScience</journal-title></journal-title-group>
    <issn pub-type="epub">2047-217X</issn>
    <publisher><publisher-name>Oxford University Press</publisher-name></publisher>
  </journal-meta>
  <article-meta>
    <article-id pub-id-type="doi">10.1093/gigascience/test1</article-id>
    <article-id pub-id-type="pmid">12345678</article-id>
    <article-id pub-id-type="pmc">9999999</article-id>
    <subj-group><subject>Technical Note</subject></subj-group>
    <title-group><article-title>A <italic>test</italic> article</article-title></title-group>
    <contrib-group>
      <contrib contrib-type="author" corresp="yes">
        <contrib-id contrib-id-type="orcid">https://orcid.org/0000-0002-1825-0097</contrib-id>
        <name><surname>Lovelace</surname><given-names>Ada</given-names></name>
        <aff><institution>Dept of Computing</institution>, Analytical Engine Lab,
             <country country="GB">UK</country></aff>
      </contrib>
      <contrib contrib-type="author">
        <name><surname>Babbage</surname><given-names>Charles</given-names></name>
        <aff><institution>Dept of Computing</institution>, Analytical Engine Lab,
             <country country="GB">UK</country></aff>
      </contrib>
    </contrib-group>
    <volume>12</volume><elocation-id>giad999</elocation-id>
    <pub-date pub-type="epub"><day>4</day><month>7</month><year>2023</year></pub-date>
    <permissions>
      <copyright-statement>(c) The Author(s) 2023.</copyright-statement>
      <copyright-year>2023</copyright-year>
      <license>
        <ali:license_ref>https://creativecommons.org/licenses/by/4.0/</ali:license_ref>
        <license-p>Open Access under CC BY.</license-p>
      </license>
    </permissions>
    <abstract><p>An abstract with maths <inline-formula><tex-math>
\\documentclass[12pt]{minimal}\\usepackage{amsmath}\\begin{document}$\\alpha$\\end{document}
</tex-math></inline-formula>.</p></abstract>
    <kwd-group><kwd>testing</kwd><kwd>parsing</kwd></kwd-group>
  </article-meta>
</front>
<body>
  <sec id="s1"><title>Introduction</title>
    <p>Text citing <xref ref-type="bibr" rid="b1">1</xref> and
       <xref ref-type="bibr" rid="b1">1 again</xref>, plus a missing
       <xref ref-type="supplementary-material" rid="nosuch">file</xref>.</p>
    <p>See <ext-link ext-link-type="uri"
        xlink:href="https://smart&lt;plxhyp&gt;PLXHYP&lt;/plxhyp&gt;api.info/x">https://smart-api.info/x</ext-link>.</p>
    <fig id="f1"><label>Figure 1:</label>
      <caption><p>A caption that stays text.</p></caption>
      <graphic xlink:href="fig1.jpg"/></fig>
    <table-wrap id="t1"><label>Table 1:</label>
      <caption><p>A table.</p></caption>
      <table><thead><tr><th/><th>A</th></tr></thead>
        <tbody><tr><td>x</td><td><list list-type="bullet"><list-item><p>one</p></list-item>
        <list-item><p>two</p></list-item></list></td></tr></tbody></table></table-wrap>
    <disp-formula id="e1"><tex-math>
\\documentclass[12pt]{minimal}\\begin{document}}{}\\begin{eqnarray*} y = mx + c \\end{eqnarray*}\\end{document}
</tex-math></disp-formula>
  </sec>
</body>
<back>
  <ref-list><ref id="b1"><mixed-citation publication-type="journal">
    <string-name><surname>Turing</surname><given-names>AM</given-names></string-name>.
    <article-title>Computing machinery</article-title>. <source>Mind</source>.
    <year>1950</year>;<volume>59</volume>:<fpage>433</fpage>.
    <pub-id pub-id-type="doi">10.1093/mind/LIX.236.433</pub-id></mixed-citation></ref>
  </ref-list>
  <ack><title>Acknowledgements</title><p>Thanks.</p></ack>
</back>
</article>"""


@pytest.fixture(scope="module")
def art():
    return JatsParser("PMC9999999").parse(ARTICLE)


# -- front matter ------------------------------------------------------

def test_title_and_ids(art):
    assert art.front.title_text == "A test article"
    assert art.front.doi == "10.1093/gigascience/test1"
    assert art.front.pmcid == "PMC9999999"
    assert art.front.volume == "12"


def test_affiliations_deduplicated_and_bound(art):
    """Two authors sharing one id-less nested <aff> must end up pointing at a
    single normalised affiliation."""
    assert len(art.front.affiliations) == 1
    aff = art.front.affiliations[0]
    assert aff.id
    assert all(au.affiliation_ids == [aff.id] for au in art.front.authors)
    # Punctuation from element tails must not gain a leading space.
    assert " ," not in aff.text
    assert "Dept of Computing, Analytical Engine Lab" in aff.text


def test_orcid_and_corresponding(art):
    a = art.front.authors[0]
    assert a.orcid == "0000-0002-1825-0097"
    assert a.corresponding is True
    assert art.front.authors[1].corresponding is False


def test_license_from_ali_namespace(art):
    """The licence URL lives in ali:license_ref, not xlink:href."""
    assert art.front.license.url == "https://creativecommons.org/licenses/by/4.0/"
    assert art.front.license.code == "CC BY"
    assert art.front.license.copyright_statement.startswith("(c) The Author(s)")


def test_citation_line_carries_attribution(art):
    c = art.front.citation()
    assert "Ada Lovelace" in c and "A test article" in c
    assert "10.1093/gigascience/test1" in c


# -- body --------------------------------------------------------------

def test_sections_and_floats(art):
    blocks = list(walk_blocks(art.body))
    assert any(isinstance(b, Section) for b in blocks)
    figs = [b for b in blocks if isinstance(b, Figure)]
    assert len(figs) == 1 and figs[0].label == "Figure 1:"
    assert inline_text([]) == ""
    # The caption is kept as text, never folded into the image.
    from journal2epub.model import block_text
    assert "caption that stays text" in block_text(figs[0].caption)


def test_table_structure_and_block_content_in_cell(art):
    tbl = next(b for b in walk_blocks(art.body) if isinstance(b, Table))
    assert tbl.column_count == 2
    assert tbl.rows[0].in_header
    # A list inside a cell is flattened onto lines rather than dropped.
    cell = tbl.rows[1].cells[1]
    assert "one" in inline_text(cell.children) and "two" in inline_text(cell.children)


def test_citations_get_backlink_anchors(art):
    ref = art.references[0]
    assert ref.id == "b1"
    assert len(ref.cited_by) == 2, "both citations of b1 should be recorded"
    links = [n for n in walk_inlines(art.body)
             if isinstance(n, InternalLink) and n.kind == "bibr"]
    assert [l.anchor for l in links] == ref.cited_by


def test_reference_fields_parsed(art):
    r = art.references[0]
    assert r.doi == "10.1093/mind/LIX.236.433"
    assert r.year == "1950" and r.source == "Mind"
    assert "Turing" in inline_text(r.text)


def test_back_matter_kept(art):
    assert any("Acknowledgements" in inline_text(s.title) for s in art.back)


def test_plxhyp_artifact_stripped_from_text(art):
    """The hyphenation-point marker must not reach the reader."""
    txt = " ".join(inline_text([n]) for n in walk_inlines(art.body))
    assert "PLXHYP" not in txt
    links = [n for n in walk_inlines(art.body) if isinstance(n, ExternalLink)]
    assert any("smart-api.info" in l.href for l in links)


def test_unhandled_log_is_empty_for_known_shapes(art):
    assert art.unhandled == [], f"unexpected unhandled: {art.unhandled}"


# -- maths -------------------------------------------------------------

def test_tex_extracted_from_document_wrapper(art):
    blocks = all_blocks(art)
    inline = [n for n in walk_inlines(blocks) if isinstance(n, InlineMath)]
    assert inline and inline[0].source == r"\alpha"
    assert "documentclass" not in inline[0].source


def test_stray_brace_artifact_removed(art):
    disp = [b for b in walk_blocks(art.body) if isinstance(b, DisplayMath)]
    assert disp
    src = disp[0].source
    assert src.startswith(r"\begin{eqnarray*}"), src
    assert "}{}" not in src


def test_abstract_maths_is_collected_for_rendering(art):
    """Maths in the abstract must be reachable from `all_blocks`, or it never
    reaches the typesetter."""
    found = [n for n in walk_inlines(all_blocks(art)) if isinstance(n, InlineMath)]
    body_only = [n for n in walk_inlines(art.body) if isinstance(n, InlineMath)]
    assert len(found) > len(body_only)


@pytest.mark.parametrize("raw,expected", [
    (r"\documentclass[12pt]{minimal}\begin{document}$x^2$\end{document}", "x^2"),
    (r"\begin{document}}{}$e$\end{document}", "e"),
    (r"\documentclass{minimal}\usepackage{amsmath}", ""),
    (r"\[y = mx\]", "y = mx"),
    (r"$$z$$", "z"),
])
def test_clean_tex(raw, expected):
    assert _clean_tex(raw) == expected


def test_strip_artifacts_passthrough():
    assert _strip_artifacts("plain text") == "plain text"
    assert _strip_artifacts("a<plxhyp>PLXHYP</plxhyp>b") == "a-b"


# -- resilience --------------------------------------------------------

def test_malformed_xml_recovers_or_raises_cleanly():
    with pytest.raises(ValueError):
        JatsParser("x").parse(b"<notanarticle/>")


def test_unknown_element_is_recorded_not_dropped():
    xml = ARTICLE.replace("<p>Thanks.</p>", "<weird-thing>content here</weird-thing>")
    a = JatsParser("PMC1").parse(xml)
    tags = {u.tag for u in a.unhandled}
    assert "weird-thing" in tags
    # ...and its text survives into the document rather than vanishing.
    from journal2epub.model import block_text
    assert "content here" in block_text(a.back)
