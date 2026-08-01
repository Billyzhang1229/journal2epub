"""Stylesheet generation from a theme.

The theme carries colour, type stack, scale and rule weights; this module turns
one into CSS. Templates never mention a colour or a typeface, so adding a
journal means writing a theme, not editing markup.

Deliberate constraints, for e-ink and older readers:
  * no web fonts — reader-installed fonts only, so nothing to licence or embed
    and nothing that fails to load on a device with a fixed font list;
  * no CSS the EPUB 3 core profile does not require;
  * every colour must survive being rendered as greyscale;
  * `max-width: 100%` on everything that could otherwise force a horizontal
    scroll of the page body.
"""
from __future__ import annotations

from ..config import Theme


def stylesheet(t: Theme) -> str:
    h1, h2, h3, h4 = (t.scale + ["1em"] * 4)[:4]
    return f"""/* {t.name or t.key} — generated from the theme; do not edit by hand. */

html {{
  font-family: {t.body_stack};
  font-size: {t.base_size};
  line-height: {t.line_height};
  color: {t.ink};
  background: {t.paper};
}}
body {{
  margin: 0 5%; text-align: left; widows: 2; orphans: 2;
  /* Scientific prose is full of tokens with no break opportunity — URLs,
     accession numbers, supplementary filenames, tool names. On a 6" e-ink
     screen a single one of them will otherwise widen the whole page and force
     the body to scroll sideways. */
  overflow-wrap: break-word;
}}
/* `anywhere`, not `break-word`: only `anywhere` also shrinks the element's
   min-content width, which is what stops a long URL widening its container. */
a, code, pre, .supplement .label, .references li, .keywords, .attribution {{
  overflow-wrap: anywhere;
}}

/* ---------- headings ---------- */
h1, h2, h3, h4, h5, h6 {{
  font-family: {t.heading_stack};
  line-height: 1.2;
  page-break-after: avoid;
  break-after: avoid;
  margin: 1.4em 0 .5em;
  color: {t.ink};
}}
h1 {{ font-size: {h1}; margin-top: 0; }}
h2 {{ font-size: {h2}; }}
h3 {{ font-size: {h3}; }}
h4, h5, h6 {{ font-size: {h4}; }}

p {{ margin: 0 0 .85em; }}
p + p {{ text-indent: 0; }}
/* Links carry a non-colour cue. Colour alone fails contrast against the
   surrounding text, and is invisible on a greyscale e-ink panel. */
a {{ color: {t.accent}; text-decoration: underline; }}
/* Citation markers, back-links and reference tails read as apparatus rather
   than prose, and are already set apart by position and size. */
/* The underline requirement applies to links sitting *inside* a block of text,
   where colour alone would not distinguish them. Standalone links — contents
   entries, navigation, citation markers — are already unambiguous. */
a.cite, .backlinks a, .ref-links a, .affiliations a, sup a, a.orcid,
.toc-entry a, nav a {{
  text-decoration: none;
}}
a.orcid {{ border-bottom: 1px dotted {t.muted}; }}

/* ---------- article opener ---------- */
.opener {{ margin-bottom: 1.6em; }}
.article-type {{
  font-family: {t.sans_stack};
  text-transform: {t.label_transform};
  letter-spacing: {t.label_letterspacing};
  font-weight: {t.label_weight};
  font-size: {t.label_size};
  color: {t.accent};
  margin: 0 0 .7em;
}}
.opener h1 {{ margin: 0 0 .5em; }}
.byline {{
  font-family: {t.sans_stack};
  font-size: .92em;
  line-height: 1.45;
  margin: 0 0 .5em;
  color: {t.ink};
}}
.byline .orcid {{ color: {t.muted}; font-size: .85em; }}
.affiliations {{
  font-size: .78em; color: {t.muted}; line-height: 1.4;
  margin: 0 0 1em; padding: 0; font-family: {t.sans_stack};
  /* The superscript marker in each item is the numbering; a list marker as
     well would render "1. ¹ Department of…". */
  list-style: none;
}}
.affiliations li {{ margin-bottom: .2em; }}
.opener-rule {{
  border: 0; border-top: {t.heavy_rule_weight} solid {t.accent};
  margin: 0 0 1.2em; height: 0;
}}

/* ---------- abstract ---------- */
.abstract {{
  font-size: .95em;
  border-left: {t.heavy_rule_weight} solid {t.rule};
  padding-left: 1em;
  margin: 0 0 1.6em;
}}
.abstract h2 {{
  font-size: {t.label_size};
  text-transform: {t.label_transform};
  letter-spacing: {t.label_letterspacing};
  font-weight: {t.label_weight};
  color: {t.muted};
  margin: 0 0 .5em;
}}
.keywords {{ font-size: .8em; color: {t.muted}; margin: 0 0 1.5em; }}
.keywords .kw-label {{ font-weight: 700; }}

/* ---------- figures ---------- */
figure {{
  margin: 1.6em 0;
  page-break-inside: avoid;
  break-inside: avoid;
  text-align: center;
}}
figure img {{
  max-width: 100%;
  height: auto;
  object-fit: contain;
}}
figcaption {{
  font-family: {t.sans_stack};
  font-size: .78em;
  line-height: 1.45;
  text-align: left;
  color: {t.ink};
  margin-top: .6em;
}}
figcaption .label, .table-label {{
  font-weight: 700;
  color: {t.accent};
}}
.fig-missing {{
  border: {t.rule_weight} dashed {t.rule};
  padding: 1em; font-size: .8em; color: {t.muted};
  font-family: {t.sans_stack};
}}

/* ---------- tables ----------
   Wide scientific tables cannot reflow onto a phone or a 6" e-ink screen.
   The strategy is deliberate: never let a table widen the page body, give it
   its own scroll region, and step the type down as the column count rises. */
.table-wrap {{ margin: 1.6em 0; page-break-inside: avoid; break-inside: avoid; }}
.table-scroll {{ overflow-x: auto; max-width: 100%; }}
table {{
  border-collapse: collapse;
  font-family: {t.sans_stack};
  font-size: .78em;
  line-height: 1.35;
  margin: .4em 0;
}}
th, td {{
  border-top: {t.rule_weight} solid {t.rule};
  padding: .3em .5em;
  text-align: left;
  vertical-align: top;
}}
thead th {{
  border-bottom: 2px solid {t.ink};
  border-top: 2px solid {t.ink};
  font-weight: 700;
}}
tbody tr:last-child td {{ border-bottom: {t.rule_weight} solid {t.rule}; }}
.cols-md table {{ font-size: .72em; }}
.cols-lg table {{ font-size: .66em; }}
.cols-lg th, .cols-lg td {{ padding: .25em .35em; }}
.cols-xl table {{ font-size: .6em; }}
.cols-xl th, .cols-xl td {{ padding: .2em .3em; }}
.table-note {{
  font-size: .72em; color: {t.muted}; font-family: {t.sans_stack};
  line-height: 1.4; margin-top: .4em;
}}
.wide-note {{
  font-size: .7em; color: {t.muted}; font-style: italic;
  font-family: {t.sans_stack}; margin: .2em 0 .4em;
}}

/* ---------- maths ---------- */
.math-display {{
  display: block; text-align: center; margin: 1.2em 0;
  overflow-x: auto; max-width: 100%;
}}
.math-inline {{ display: inline-block; vertical-align: -0.25ex; }}
.math-display svg, .math-inline svg {{ max-width: 100%; }}
.math-fallback {{
  font-family: {t.mono_stack}; font-size: .85em;
  background: transparent; color: {t.ink};
  border-bottom: 1px dotted {t.muted};
}}

/* ---------- lists, quotes, code ---------- */
ul, ol {{ margin: 0 0 .9em 1.4em; padding: 0; }}
li {{ margin-bottom: .3em; }}
blockquote {{
  margin: 1.2em 0; padding-left: 1em;
  border-left: {t.heavy_rule_weight} solid {t.rule};
  color: {t.ink};
}}
pre, code {{ font-family: {t.mono_stack}; font-size: .82em; }}
pre {{
  white-space: pre-wrap; word-wrap: break-word; overflow-x: auto;
  border-left: {t.rule_weight} solid {t.rule}; padding-left: .8em;
}}
dl dt {{ font-weight: 700; margin-top: .6em; }}
dl dd {{ margin: 0 0 .4em 1.2em; }}

/* ---------- boxed material ---------- */
.boxed {{
  border: {t.rule_weight} solid {t.rule};
  padding: .9em 1em; margin: 1.4em 0;
  page-break-inside: avoid; break-inside: avoid;
}}
.boxed .boxed-title {{
  font-family: {t.sans_stack}; font-weight: 700;
  font-size: .9em; margin: 0 0 .5em;
}}

/* ---------- supplementary ---------- */
.supplement {{
  border-left: {t.rule_weight} solid {t.rule};
  padding-left: .9em; margin: 1.2em 0;
  font-size: .85em; font-family: {t.sans_stack};
}}
.supplement .label {{ font-weight: 700; }}
.supplement .supp-note {{ color: {t.muted}; font-size: .9em; }}

/* ---------- references ---------- */
.references ol {{ margin-left: 1.6em; }}
.references li {{ margin-bottom: .5em; font-size: .85em; line-height: 1.4; }}
.backlinks {{ font-size: .85em; }}
.backlinks a {{ color: {t.muted}; }}
sup a, .cite {{ text-decoration: none; }}

/* ---------- footnotes ---------- */
.footnotes {{ font-size: .82em; color: {t.ink}; }}
.footnotes li {{ margin-bottom: .4em; }}

/* ---------- attribution block ---------- */
.attribution {{
  margin-top: 2.4em;
  padding-top: .8em;
  border-top: {t.rule_weight} solid {t.rule};
  font-family: {t.sans_stack};
  font-size: .74em;
  line-height: 1.5;
  color: {t.muted};
}}
.attribution .cite-line {{ color: {t.ink}; }}
.attribution a {{ color: {t.muted}; }}

/* ---------- front matter / TOC ---------- */
.volume-title {{ text-align: center; margin: 2em 0; }}
.volume-title .journal-name {{
  font-family: {t.sans_stack};
  text-transform: {t.label_transform};
  letter-spacing: {t.label_letterspacing};
  font-weight: {t.label_weight};
  font-size: 1.1em;
  color: {t.accent};
  margin-bottom: .4em;
}}
.volume-title .volume-line {{ font-size: 1.5em; margin: .3em 0; }}
.volume-title .edition-note {{
  font-size: .8em; color: {t.muted}; margin-top: 1.5em;
  font-family: {t.sans_stack};
}}
nav[epub|type~="toc"] ol {{ list-style: none; margin-left: 0; }}
.toc-part {{
  font-family: {t.sans_stack};
  text-transform: {t.label_transform};
  letter-spacing: {t.label_letterspacing};
  font-weight: {t.label_weight};
  font-size: {t.label_size};
  color: {t.accent};
  border-bottom: {t.rule_weight} solid {t.rule};
  padding-bottom: .3em;
  margin: 1.8em 0 .8em;
}}
.toc-entry {{ margin-bottom: .9em; }}
.toc-entry .toc-title {{ display: block; line-height: 1.3; }}
.toc-entry .toc-authors {{
  display: block; font-size: .78em; color: {t.muted};
  font-family: {t.sans_stack}; margin-top: .15em;
}}
.colophon {{ font-size: .85em; }}
.colophon dt {{ margin-top: .8em; }}
.missing-list li {{ font-size: .82em; margin-bottom: .5em; }}
.missing-list .why {{ color: {t.muted}; }}

/* ---------- dark mode ----------
   Readers that invert the page should get the accent adjusted with it. */
@media (prefers-color-scheme: dark) {{
  html {{ color: {t.paper}; background: {t.ink}; }}
  a, .article-type, figcaption .label, .toc-part {{ color: {t.accent_dark}; }}
  .opener-rule {{ border-top-color: {t.accent_dark}; }}
}}
"""
