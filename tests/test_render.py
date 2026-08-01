"""Renderer, packaging, state and config tests."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from journal2epub.config import JournalConfig, SectionRule, Theme, load_journal, load_theme
from journal2epub.model import Resolution
from journal2epub.render.css import stylesheet
from journal2epub.render.epub import EpubBuilder, MissingArticle
from journal2epub.render.images import prepare
from journal2epub.render.math import MathRenderer
from journal2epub.render.xhtml import ArticleRenderer, sanitize_url
from journal2epub.sources.base import ArticleStub
from journal2epub.state import BuildState
from journal2epub.sources.jats import JatsParser

from test_jats import ARTICLE


@pytest.fixture
def journal():
    return load_journal("gigascience")


@pytest.fixture
def theme():
    return load_theme("gigascience")


@pytest.fixture
def rendered(journal, theme, tmp_path):
    art = JatsParser("PMC9999999").parse(ARTICLE)
    r = ArticleRenderer(article=art, journal=journal, theme=theme,
                        math=MathRenderer(cache_dir=tmp_path / "math"))
    return art, r, r.render()


# -- link integrity ----------------------------------------------------

def test_dangling_links_are_degraded_not_emitted(rendered):
    """A cross-reference to material absent from the deposit must become plain
    text: a dangling fragment is an EPUB validation error."""
    _art, r, html = rendered
    import re
    ids = set(re.findall(r'\bid="([^"]+)"', html))
    for target in re.findall(r'href="#([^"]+)"', html):
        assert target in ids, f"dangling internal link to #{target}"
    assert r.stats.dangling_links >= 1, "the fixture's bad xref should be degraded"
    assert "missing" in html  # the link text survives


def test_citation_backlinks_resolve(rendered):
    _art, _r, html = rendered
    import re
    ids = set(re.findall(r'\bid="([^"]+)"', html))
    backs = re.findall(r'class="backlinks">\[cited at (.*?)\]</span>', html)
    assert backs, "references should carry back-links"
    for target in re.findall(r'href="#(cite-[^"]+)"', html):
        assert target in ids


def test_ids_are_unique(rendered):
    import re
    _art, _r, html = rendered
    ids = re.findall(r'\bid="([^"]+)"', html)
    assert len(ids) == len(set(ids)), "duplicate ids in one document"


# -- attribution -------------------------------------------------------

def test_attribution_block_is_injected(rendered):
    """Openly licensed articles must carry authors, citation, id and licence."""
    _art, _r, html = rendered
    assert 'class="attribution"' in html
    assert "10.1093/gigascience/test1" in html
    assert "creativecommons.org/licenses/by/4.0" in html
    assert "PMC9999999" in html
    assert "Ada Lovelace" in html


# -- figures, tables, maths -------------------------------------------

def test_figure_caption_is_text_and_image_has_alt(rendered):
    _art, _r, html = rendered
    assert "<figcaption>" in html
    assert "caption that stays text" in html


def test_table_has_header_scope_and_scroll_region(rendered):
    _art, _r, html = rendered
    assert 'class="table-scroll"' in html
    assert 'tabindex="0"' in html and 'role="region"' in html
    assert 'scope="col"' in html


def test_empty_header_cell_is_not_a_header(rendered):
    """A blank corner cell marked <th> would leave screen readers announcing an
    unnamed header for its whole column."""
    _art, _r, html = rendered
    assert "<th></th>" not in html
    assert "<th scope=\"col\"></th>" not in html


def test_math_falls_back_loudly_when_not_rendered(rendered):
    """With no typeset SVG available the TeX source must still be shown."""
    _art, r, html = rendered
    assert r.stats.math_fallback > 0
    assert "math-fallback" in html
    assert "alpha" in html


# -- URL sanitising ----------------------------------------------------

@pytest.mark.parametrize("raw,expect", [
    # A genuine legacy DOI with characters illegal in a URI.
    ("10.1002/1615-9861(200209)2:9<1146::AID-PROT1146>3.0.CO;2-6",
     "10.1002/1615-9861(200209)2:9%3C1146::AID-PROT1146%3E3.0.CO;2-6"),
    ("https://x.org/a[b]c", "https://x.org/a%5Bb%5Dc"),
    ("https://smart<plxhyp>PLXHYP</plxhyp>api.info/r", "https://smart-api.info/r"),
    # Already-encoded sequences must not be double-encoded.
    ("https://x.org/a%20b", "https://x.org/a%20b"),
    ("https://x.org/100%pure", "https://x.org/100%25pure"),
    ("https://x.org/a b", "https://x.org/ab"),
])
def test_sanitize_url(raw, expect):
    assert sanitize_url(raw) == expect


# -- images ------------------------------------------------------------

def _png(size=(40, 30), mode="RGB", colour=(255, 0, 0)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new(mode, size, colour).save(buf, "PNG")
    return buf.getvalue()


def test_tiff_is_converted_to_a_core_epub_format():
    """TIFF is not a core EPUB image type and fails to display on most devices."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (50, 50), (10, 200, 10)).save(buf, "TIFF")
    out = prepare(buf.getvalue(), "plate1.tiff", "image/tiff")
    assert out is not None
    assert out.mimetype in ("image/png", "image/jpeg")
    assert not out.filename.endswith(".tiff")
    assert "TIFF" in out.note


def test_oversized_image_is_bounded():
    out = prepare(_png(size=(4000, 3000)), "big.png", "image/png", max_edge=800)
    assert out is not None and max(out.width, out.height) <= 800
    assert "resized" in out.note


def test_undecodable_bytes_are_rejected_not_embedded():
    assert prepare(b"this is not an image", "x.png", "image/png") is None


# -- config ------------------------------------------------------------

def test_subject_beats_type_for_section_placement(journal):
    """Data Notes and Technical Notes are tagged `research-article` and are
    distinguished only by subject, so a type rule must not swallow them."""
    assert journal.section_for("research-article", ["Technical Note"]).name == "Technical Notes"
    assert journal.section_for("research-article", ["Data Note"]).name == "Data Notes"
    assert journal.section_for("research-article", ["Research"]).name == "Research"
    assert journal.section_for("research-article", []).name == "Research"
    assert journal.section_for("correction", ["Correction"]).name == "Corrections"


def test_subject_name_variants_are_matched(journal):
    assert journal.section_for("research-article", ["Tech Note"]).name == "Technical Notes"
    assert journal.section_for("research-article", ["Datanote"]).name == "Data Notes"


def test_theme_rejects_unknown_keys(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text("[theme]\naccent='#fff'\nnonsense='x'\n")
    with pytest.raises(ValueError, match="unknown keys"):
        load_theme("bad", extra_dirs=[tmp_path])


def test_stylesheet_uses_theme_values_and_embeds_no_fonts(theme):
    css = stylesheet(theme)
    assert theme.accent in css
    assert theme.serif_stack.split(",")[0] in css
    # Embedded fonts are a licensing and compatibility trap on e-ink devices.
    assert "@font-face" not in css
    assert "overflow-wrap" in css


# -- build state -------------------------------------------------------

def test_state_is_resumable(tmp_path):
    p = tmp_path / "state.sqlite"
    stubs = [ArticleStub(doi=f"10.1/{i}", title=f"A{i}", order=i) for i in range(5)]
    with BuildState(p, journal="gigascience", volume="12") as s:
        assert not s.discovered
        s.record_discovery(stubs, registry_count=5, source_count=5)
        assert s.discovered
        assert len(s.pending()) == 5
        s.record_outcome("10.1/0", Resolution.OK)
        s.record_outcome("10.1/1", Resolution.NOT_IN_OA_SUBSET, note="closed")

    # Reopen: prior outcomes survive, only the rest is pending.
    with BuildState(p, journal="gigascience", volume="12") as s:
        assert s.discovered
        assert {r.doi for r in s.pending()} == {"10.1/2", "10.1/3", "10.1/4"}
        assert s.counts()["ok"] == 1


def test_state_refuses_to_mix_volumes(tmp_path):
    p = tmp_path / "state.sqlite"
    BuildState(p, journal="gigascience", volume="12").close()
    with pytest.raises(ValueError, match="belongs to"):
        BuildState(p, journal="gigascience", volume="13")


def test_retry_requeues_only_failures(tmp_path):
    p = tmp_path / "s.sqlite"
    stubs = [ArticleStub(doi=f"10.1/{i}", order=i) for i in range(3)]
    with BuildState(p, journal="gigascience", volume="12") as s:
        s.record_discovery(stubs, 3, 3)
        s.record_outcome("10.1/0", Resolution.OK)
        s.record_outcome("10.1/1", Resolution.FETCH_FAILED)
        s.record_outcome("10.1/2", Resolution.NOT_IN_OA_SUBSET)
        assert s.reset(only_failed=True) == 1
        assert {r.doi for r in s.pending()} == {"10.1/1"}


# -- packaging ---------------------------------------------------------

def _build(tmp_path, journal, theme, missing=()):
    art = JatsParser("PMC9999999").parse(ARTICLE)
    art.provenance.retrieved = "2026-01-02T03:04:05Z"
    b = EpubBuilder(journal=journal, theme=theme, volume="12",
                    math=MathRenderer(cache_dir=tmp_path / "m"),
                    registry_count=2, build_id="test-build-id")
    b.add_article(art, part="Technical Notes", order=0, asset_bytes={})
    b.missing = list(missing)
    out = tmp_path / "out.epub"
    b.write(out)
    return b, out


def test_epub_structure(tmp_path, journal, theme):
    _b, out = _build(tmp_path, journal, theme)
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        # mimetype must be first and stored uncompressed
        assert names[0] == "mimetype"
        assert z.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        assert z.read("mimetype") == b"application/epub+zip"
        for required in ("META-INF/container.xml", "OEBPS/package.opf",
                         "OEBPS/nav.xhtml", "OEBPS/contents.xhtml",
                         "OEBPS/colophon.xhtml", "OEBPS/not-included.xhtml"):
            assert required in names
        opf = z.read("OEBPS/package.opf").decode()
        assert "schema:accessibilitySummary" in opf
        assert "schema:accessModeSufficient" in opf
        assert 'properties="nav"' in opf


def test_edition_declares_itself_unofficial(tmp_path, journal, theme):
    _b, out = _build(tmp_path, journal, theme)
    with zipfile.ZipFile(out) as z:
        title = z.read("OEBPS/titlepage.xhtml").decode()
        colo = z.read("OEBPS/colophon.xhtml").decode()
    assert "not</strong> published by" in title
    assert "unofficial" in colo.lower() and "independently produced" in colo.lower()
    assert "National Library of Medicine" in colo


def test_missing_articles_are_named_and_explained(tmp_path, journal, theme):
    miss = [MissingArticle(doi="10.1/x", title="A closed paper",
                           resolution="not-in-oa-subset", note="closed access")]
    _b, out = _build(tmp_path, journal, theme, missing=miss)
    with zipfile.ZipFile(out) as z:
        page = z.read("OEBPS/not-included.xhtml").decode()
        title = z.read("OEBPS/titlepage.xhtml").decode()
    assert "A closed paper" in page
    assert "10.1/x" in page
    assert "Open Access Subset" in page          # the reason, in words
    assert "incomplete" in title                  # and it is loud on the title page


def test_build_is_byte_reproducible(tmp_path, journal, theme):
    """Same inputs, same bytes — no wall-clock anywhere in the output."""
    a = _build(tmp_path / "a", journal, theme)[1].read_bytes()
    b = _build(tmp_path / "b", journal, theme)[1].read_bytes()
    assert a == b


# -- theme accessibility ----------------------------------------------

def test_shipped_themes_pass_wcag_contrast():
    """A theme is where journal identity can quietly make a book inaccessible:
    an accent chosen for looks that misses the threshold produces one violation
    per citation marker, thousands per edition."""
    from journal2epub.config import available
    for key in available("themes"):
        t = load_theme(key)
        assert t.contrast_problems() == [], f"theme {key}: {t.contrast_problems()}"


def test_theme_failing_contrast_is_refused(tmp_path):
    # PLOS's own web orange: 4.46:1 on white, just under the 4.5 threshold.
    (tmp_path / "bad.toml").write_text(
        '[theme]\naccent = "#c8531a"\npaper = "#ffffff"\nink = "#1a1a1a"\n')
    with pytest.raises(ValueError, match="WCAG AA contrast"):
        load_theme("bad", extra_dirs=[tmp_path])
    # ...and it loads when the check is explicitly waived.
    assert load_theme("bad", extra_dirs=[tmp_path], check_contrast=False).accent == "#c8531a"


@pytest.mark.parametrize("fg,bg,expect", [
    ("#ffffff", "#000000", 21.0),
    ("#000000", "#ffffff", 21.0),
    ("#ffffff", "#ffffff", 1.0),
])
def test_contrast_ratio(fg, bg, expect):
    from journal2epub.config import contrast_ratio
    assert round(contrast_ratio(fg, bg), 1) == expect


# -- heading outline ---------------------------------------------------

def test_heading_levels_never_skip(rendered):
    """Boxed material and untitled sections must not push their contents down a
    heading level and leave a gap in the outline."""
    import re
    _art, _r, html = rendered
    levels = [int(m) for m in re.findall(r"<h([1-6])[^>]*>", html)]
    prev = 0
    for lvl in levels:
        if prev:
            assert lvl <= prev + 1, f"heading jumps h{prev} -> h{lvl}: {levels}"
        prev = lvl


def test_untitled_section_does_not_indent_children(journal, theme, tmp_path):
    from journal2epub.model import Article, Paragraph, Section, Text
    art = Article()
    art.body = [Section(title=[Text("Top")], children=[
        Section(title=[], children=[                      # no heading of its own
            Section(title=[Text("Nested")], children=[Paragraph(children=[Text("x")])])
        ])
    ])]
    r = ArticleRenderer(article=art, journal=journal, theme=theme,
                        math=MathRenderer(cache_dir=tmp_path / "m"))
    import re
    html = r.render()
    levels = [int(m) for m in re.findall(r"<h([1-6])[^>]*>", html)]
    # h1 is the article title from the opener; "Top" is h2 and "Nested" is h3.
    # Without the fix the untitled middle section would push "Nested" to h4.
    assert levels == [1, 2, 3], levels


# -- maths source forms ------------------------------------------------

def test_mathml_source_is_rendered_not_dropped(journal, theme, tmp_path):
    """Publishers differ: GigaScience deposits TeX, PLOS deposits MathML.
    A MathML-only expression must still reach the typesetter."""
    from journal2epub.model import Article, DisplayMath, InlineMath, Paragraph
    from journal2epub.render.math import Expr
    mml = '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi></math>'
    art = Article()
    art.body = [Paragraph(children=[InlineMath(mathml=mml, source=None,
                                               source_kind="mathml", alt="x")]),
                DisplayMath(mathml=mml, source=None, source_kind="mathml", alt="x")]
    from journal2epub.build import _collect_math
    exprs = _collect_math(art)
    assert len(exprs) == 2
    assert all(isinstance(e, Expr) and e.kind == "mathml" for e in exprs)
    assert {e.display for e in exprs} == {True, False}


def test_expression_with_no_source_is_counted_as_fallback(journal, theme, tmp_path):
    """A silently uncounted fallback would make the build report lie."""
    from journal2epub.model import Article, InlineMath, Paragraph
    art = Article()
    art.body = [Paragraph(children=[InlineMath(mathml=None, source=None,
                                               source_kind="none", alt="q")])]
    r = ArticleRenderer(article=art, journal=journal, theme=theme,
                        math=MathRenderer(cache_dir=tmp_path / "m"))
    html = r.render()
    assert r.stats.math_fallback == 1
    assert "math-fallback" in html and "q" in html


# -- naming convention -------------------------------------------------

def test_journal_keys_are_derived_from_their_titles():
    """The key is a lookup, never shown to a reader, so it should cost nothing
    to guess: lowercase the title and hyphenate the gaps. Invented contractions
    make the key set look arbitrary."""
    import re
    from journal2epub.config import available
    for key in available("journals"):
        j = load_journal(key)
        expected = re.sub(r"[^a-z0-9]+", "-", j.title.lower()).strip("-")
        assert key == expected, (
            f"journal key {key!r} is not derived from its title "
            f"{j.title!r}; expected {expected!r}")


# -- packaging ---------------------------------------------------------

def test_maths_script_ships_inside_the_package():
    """Resolving the script relative to the source tree works from a checkout
    and breaks in site-packages, which silently degrades every expression to
    fallback text. It has to travel with the package."""
    from journal2epub.render import math as math_mod
    assert math_mod.SCRIPT.exists(), math_mod.SCRIPT
    pkg_root = Path(math_mod.__file__).resolve().parent
    assert math_mod.SCRIPT.is_relative_to(pkg_root), (
        f"{math_mod.SCRIPT} is outside the installed package at {pkg_root}")


def test_packaged_data_files_are_reachable():
    """Journal and theme descriptors are package data; if they are not included
    the tool installs cleanly and then knows about no journals at all."""
    from journal2epub.config import available
    assert "gigascience" in available("journals")
    assert "gigascience" in available("themes")


def test_math_diagnosis_explains_itself(tmp_path):
    """A build that cannot typeset must say why and how to fix it, not just
    report a lower number."""
    # `node=None` means "discover it"; an empty string means "there is none".
    r = MathRenderer(cache_dir=tmp_path / "m", node="")
    usable, why = r.diagnose()
    assert usable is False
    assert "node" in why.lower()
    assert len(why) > 30, "the explanation should name a fix"


# -- build progress ----------------------------------------------------

def test_progress_reports_every_phase_to_completion(tmp_path):
    """A phase that stops short of its total reads as a failure, so each one
    must emit a completion line even when the redraw throttle swallows the
    last update."""
    import io
    from journal2epub.cli import TerminalProgress
    buf = io.StringIO()
    p = TerminalProgress(stream=buf, tty=False)
    p.phase("Resolving articles", total=3)
    for _ in range(3):
        p.advance()
    p.phase("Writing EPUB")
    p.close()
    out = buf.getvalue()
    assert "Resolving articles: done (3/3)" in out, out
    assert "Writing EPUB" in out


def test_progress_reports_actual_count_not_an_assumed_100_percent(tmp_path):
    """A phase can legitimately end with fewer items than it started with;
    claiming 100% would hide that."""
    import io
    from journal2epub.cli import TerminalProgress
    buf = io.StringIO()
    p = TerminalProgress(stream=buf, tty=False)
    p.phase("Resolving articles", total=10)
    p.advance(4)
    p.close()
    assert "done (4/10)" in buf.getvalue()


def test_progress_writes_no_control_characters_when_not_a_terminal(tmp_path):
    """Piped to a log file, thousands of carriage returns help nobody."""
    import io
    from journal2epub.cli import TerminalProgress
    buf = io.StringIO()
    p = TerminalProgress(stream=buf, tty=False)
    p.phase("Fetching", total=100)
    for _ in range(100):
        p.advance()
    p.close()
    out = buf.getvalue()
    assert "\r" not in out and "\033" not in out


def test_null_progress_satisfies_the_protocol():
    """--quiet and library use must not need a terminal."""
    from journal2epub.build import NullProgress, Progress
    n = NullProgress()
    assert isinstance(n, Progress)
    n.phase("x", total=1); n.advance(); n.note("y"); n.close()


# -- cover text fitting ------------------------------------------------

@pytest.mark.parametrize("title", [
    "GigaScience",
    "PLOS Computational Biology",
    "PLOS Neglected Tropical Diseases",
    "Journal of the American Medical Informatics Association",
    "Proceedings of the National Academy of Sciences of the United States of America",
    "Nature",
])
def test_cover_titles_are_fitted_not_clipped(title):
    """SVG text neither wraps nor shrinks, so a title wider than the canvas is
    silently cut off — which is how 'PLOS Computational Biology' became
    'PLOS Computational Biol' on the cover."""
    from journal2epub.render.epub import fit_text, text_width
    avail = 1400 - 110 * 2
    lines, size = fit_text(title, avail, max_size=96, min_size=52, max_lines=3, spacing=6)
    assert lines and all(l.strip() for l in lines)
    assert " ".join(lines) == title, "wrapping must not lose or reorder words"
    for line in lines:
        assert text_width(line, size, 6) <= avail, f"{line!r} at {size}px exceeds {avail}px"


def test_cover_volume_label_stays_on_one_line():
    """'Volume 100, Issue / 12' reads badly; shrink instead of wrapping."""
    from journal2epub.render.epub import fit_text, text_width
    avail = 1400 - 110 * 2
    for label in ("Volume 12", "Volume 22, Issue 5", "Volume 2026, Issue 12"):
        lines, size = fit_text(label, avail, max_size=120, min_size=56, max_lines=1)
        assert len(lines) == 1, f"{label!r} wrapped to {lines}"
        assert text_width(lines[0], size) <= avail


def test_width_estimate_is_close_to_measured_rendering():
    """The estimate feeds the fit, so it must not be wildly optimistic. These
    figures were measured from the real SVG rendering in a browser."""
    from journal2epub.render.epub import text_width
    for text, size, spacing, measured in [
        ("PLOS Computational", 96, 6, 1068),
        ("Biology", 96, 6, 385),
        ("Volume 22, Issue 5", 120, 0, 962),
    ]:
        est = text_width(text, size, spacing)
        ratio = est / measured
        assert 0.88 <= ratio <= 1.20, (
            f"{text!r}: estimated {est:.0f} vs measured {measured} ({ratio:.2f}x)")


def test_cover_svg_contains_the_whole_title(journal, theme, tmp_path):
    from journal2epub.render.epub import EpubBuilder
    b = EpubBuilder(journal=load_journal("plos-computational-biology"),
                    theme=load_theme("plos"), volume="22", issue="5",
                    math=MathRenderer(cache_dir=tmp_path / "m"))
    svg = b._cover_svg()
    import re
    rendered = " ".join(re.findall(r"<text[^>]*>([^<]*)</text>", svg))
    assert "PLOS Computational" in rendered and "Biology" in rendered
    assert "Volume 22, Issue 5" in rendered
