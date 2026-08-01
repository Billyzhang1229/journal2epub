"""Journal and theme configuration.

Adding a journal should mean writing two data files — a journal descriptor and
a theme — and, if its content lives somewhere new, one source adapter. It must
never mean editing templates or the renderer.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path


@dataclass(slots=True)
class SectionRule:
    """Maps an article's type/subject onto a named part of the volume TOC."""
    name: str
    match_types: list[str] = field(default_factory=list)
    match_subjects: list[str] = field(default_factory=list)
    order: int = 100

    def matches_subject(self, subjects: list[str]) -> bool:
        low = {s.lower() for s in subjects}
        return any(s.lower() in low for s in self.match_subjects)

    def matches_type(self, article_type: str) -> bool:
        return article_type in self.match_types

    def matches(self, article_type: str, subjects: list[str]) -> bool:
        return self.matches_type(article_type) or self.matches_subject(subjects)


@dataclass(slots=True)
class JournalConfig:
    key: str
    title: str
    issn: str
    publisher: str = ""
    nlm_ta: str = ""                     # PMC journal abbreviation, for esearch
    source: str = "pmc_jats"
    theme: str = "default"
    homepage: str = ""
    description: str = ""
    # Article types that are content but not research (framing in the reader).
    type_labels: dict[str, str] = field(default_factory=dict)
    sections: list[SectionRule] = field(default_factory=list)
    exclude_types: list[str] = field(default_factory=list)

    def label_for(self, article_type: str) -> str:
        if article_type in self.type_labels:
            return self.type_labels[article_type]
        return article_type.replace("-", " ").title()

    def section_for(self, article_type: str, subjects: list[str]) -> SectionRule:
        """Subject wins over article type: journals routinely tag Data Notes and
        Technical Notes as `research-article`, distinguishing them only by
        subject, so a type rule would otherwise swallow them."""
        ordered = sorted(self.sections, key=lambda r: r.order)
        for rule in ordered:
            if rule.matches_subject(subjects):
                return rule
        for rule in ordered:
            if rule.matches_type(article_type):
                return rule
        return SectionRule(name="Articles", order=999)


@dataclass(slots=True)
class Theme:
    """Visual identity. Colours, type stack, scale — never markup."""
    key: str
    name: str = ""
    # colour
    accent: str = "#000000"
    accent_dark: str = "#000000"
    rule: str = "#cccccc"
    muted: str = "#555555"
    ink: str = "#111111"
    paper: str = "#ffffff"
    # type
    serif_stack: str = "Georgia, 'Times New Roman', serif"
    sans_stack: str = "'Helvetica Neue', Helvetica, Arial, sans-serif"
    mono_stack: str = "'SF Mono', Menlo, Consolas, monospace"
    body_family: str = "serif"           # serif | sans
    heading_family: str = "sans"
    # scale
    base_size: str = "1em"
    line_height: str = "1.5"
    scale: list[str] = field(default_factory=lambda: ["1.75em", "1.35em", "1.15em", "1em"])
    # article-type label treatment
    label_transform: str = "uppercase"
    label_letterspacing: str = "0.09em"
    label_weight: str = "700"
    label_size: str = "0.72em"
    # rules
    rule_weight: str = "1px"
    heavy_rule_weight: str = "3px"
    # cover
    cover_bg: str = "#ffffff"
    cover_fg: str = "#111111"
    cover_accent: str = "#000000"

    @property
    def body_stack(self) -> str:
        return self.serif_stack if self.body_family == "serif" else self.sans_stack

    @property
    def heading_stack(self) -> str:
        return self.serif_stack if self.heading_family == "serif" else self.sans_stack

    def contrast_problems(self, minimum: float = 4.5) -> list[str]:
        """Colour pairs that fail WCAG AA for normal text.

        A theme is the one place where a journal's identity can quietly make the
        book inaccessible: an accent chosen for its look can miss the threshold
        by a hundredth and produce thousands of violations, one per citation
        marker. Checking here means a bad theme cannot ship.
        """
        pairs = [
            ("accent on paper", self.accent, self.paper),
            ("ink on paper", self.ink, self.paper),
            ("muted on paper", self.muted, self.paper),
            ("accent_dark on ink (dark mode)", self.accent_dark, self.ink),
            ("paper on ink (dark mode)", self.paper, self.ink),
            ("cover_fg on cover_bg", self.cover_fg, self.cover_bg),
            ("cover_accent on cover_bg", self.cover_accent, self.cover_bg),
        ]
        out = []
        for what, fg, bg in pairs:
            try:
                r = contrast_ratio(fg, bg)
            except ValueError:
                out.append(f"{what}: {fg!r} on {bg!r} is not a colour")
                continue
            if r < minimum:
                out.append(f"{what}: {fg} on {bg} is {r:.2f}:1, below {minimum}:1")
        return out


def _data_dir(kind: str) -> Path:
    return Path(str(resources.files("journal2epub") / "data" / kind))


def load_journal(key: str, extra_dirs: list[Path] | None = None) -> JournalConfig:
    path = _find(key, "journals", extra_dirs)
    raw = tomllib.loads(path.read_text())
    j = raw.get("journal", raw)
    sections = [
        SectionRule(
            name=s["name"],
            match_types=s.get("types", []),
            match_subjects=s.get("subjects", []),
            order=s.get("order", 100),
        )
        for s in raw.get("section", [])
    ]
    return JournalConfig(
        key=key,
        title=j["title"],
        issn=j["issn"],
        publisher=j.get("publisher", ""),
        nlm_ta=j.get("nlm_ta", ""),
        source=j.get("source", "pmc_jats"),
        theme=j.get("theme", "default"),
        homepage=j.get("homepage", ""),
        description=j.get("description", ""),
        type_labels=raw.get("type_labels", {}),
        sections=sections,
        exclude_types=j.get("exclude_types", []),
    )


def _relative_luminance(colour: str) -> float:
    h = colour.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError(f"not a hex colour: {colour!r}")
    try:
        parts = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    except ValueError as e:
        raise ValueError(f"not a hex colour: {colour!r}") from e
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in parts]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 2 contrast ratio between two colours."""
    a, b = _relative_luminance(fg), _relative_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def load_theme(key: str, extra_dirs: list[Path] | None = None,
               check_contrast: bool = True) -> Theme:
    path = _find(key, "themes", extra_dirs)
    raw = tomllib.loads(path.read_text())
    t = raw.get("theme", raw)
    known = {f for f in Theme.__dataclass_fields__ if f != "key"}
    unknown = set(t) - known
    if unknown:
        raise ValueError(f"theme {key!r}: unknown keys {sorted(unknown)}")
    theme = Theme(key=key, **t)
    if check_contrast:
        problems = theme.contrast_problems()
        if problems:
            raise ValueError(
                f"theme {key!r} fails WCAG AA contrast and would make every "
                f"page of the edition inaccessible:\n  - "
                + "\n  - ".join(problems))
    return theme


def _find(key: str, kind: str, extra_dirs: list[Path] | None) -> Path:
    for d in list(extra_dirs or []) + [_data_dir(kind)]:
        p = Path(d) / f"{key}.toml"
        if p.exists():
            return p
    avail = sorted(p.stem for p in _data_dir(kind).glob("*.toml"))
    raise FileNotFoundError(f"no {kind[:-1]} {key!r}; available: {', '.join(avail)}")


def available(kind: str) -> list[str]:
    return sorted(p.stem for p in _data_dir(kind).glob("*.toml"))
