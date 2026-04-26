"""
SEC 10-K / S-1 / S-4 HTML parser — extracts the 'CIM-shaped' sections that
matter for diligence.

10-Ks are a CIM proxy: same structural anatomy (business overview, risk
factors, MD&A, financials, governance) just authored by management for
public regulatory disclosure rather than by a banker for a strategic.
The parser pulls out the sections a PE diligence team would read first.

Pure stdlib — uses regex + html.parser. No bs4 dependency.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path


# Items we extract — keyed by SEC's standard 10-K item numbering.
# Note: the same logic works for S-1 too (sections numbered differently;
# the matcher tolerates both Roman and Arabic forms).
_ITEM_LABELS = {
    "1": "Business",
    "1A": "Risk Factors",
    "1B": "Unresolved Staff Comments",
    "1C": "Cybersecurity",
    "2": "Properties",
    "3": "Legal Proceedings",
    "4": "Mine Safety Disclosures",
    "5": "Market for Registrant's Common Equity",
    "6": "Selected Financial Data",
    "7": "Management's Discussion and Analysis",
    "7A": "Quantitative and Qualitative Disclosures About Market Risk",
    "8": "Financial Statements",
    "9": "Changes in and Disagreements With Accountants",
    "9A": "Controls and Procedures",
    "9B": "Other Information",
    "9C": "Disclosure Regarding Foreign Jurisdictions",
    "10": "Directors, Executive Officers and Corporate Governance",
    "11": "Executive Compensation",
    "12": "Security Ownership",
    "13": "Certain Relationships and Related Transactions",
    "14": "Principal Accountant Fees and Services",
    "15": "Exhibits, Financial Statement Schedules",
}


@dataclass(frozen=True)
class ParsedFiling:
    company_name: str
    fiscal_year_end: str          # "" if not found
    sections: dict[str, str]      # item_id ("1A") → cleaned plain-text body
    paragraphs: dict[str, list[str]]  # item_id → list of paragraphs in that section
    raw_text: str                 # the whole filing as plain text (post-HTML strip)
    char_count: int


class _TextStripper(HTMLParser):
    """Convert SEC HTML to plain text while preserving paragraph breaks.

    The SEC's filing HTML is pseudo-HTML — heavy on tables, inline styles,
    and `<font>` blocks. We treat <p>, <div>, <br>, <tr>, <li> as paragraph
    breaks; collapse other whitespace.
    """

    _PARAGRAPH_TAGS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._in_script_or_style = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in {"script", "style"}:
            self._in_script_or_style += 1
            return
        if tag in self._PARAGRAPH_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._in_script_or_style = max(0, self._in_script_or_style - 1)
            return
        if tag in self._PARAGRAPH_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_script_or_style:
            return
        self._chunks.append(data)

    def get_text(self) -> str:
        return "".join(self._chunks)


_WS = re.compile(r"[ \t\xa0​]+")
_BLANK_LINES = re.compile(r"\n[ \t]*\n+")


def _normalize_text(raw: str) -> str:
    """Collapse runs of whitespace, drop empty lines."""
    out = _WS.sub(" ", raw)
    out = _BLANK_LINES.sub("\n\n", out)
    out = "\n".join(line.strip() for line in out.splitlines())
    return out.strip()


# Match an Item header. Tolerant of:
#   "Item 1A. Risk Factors"
#   "ITEM 1A — Risk Factors"
#   "Item 1A: Risk Factors"
# Also matches a bare "Item 1A." at start of a line/paragraph.
_ITEM_RE = re.compile(
    r"(?im)^\s*item\s+(?P<num>\d{1,2}[A-C]?)[\.\):\-—\s]+(?P<title>[^\n]{0,120})$",
)


def _extract_sections(text: str) -> tuple[dict[str, str], dict[str, list[str]]]:
    """
    Walk the cleaned plain-text and slice by Item N. headers.

    SEC filings often repeat "Item 1A. Risk Factors" in a table-of-contents
    AND in the body. We handle this by always preferring the LATER (longer)
    occurrence per item — the body content, not the TOC entry.
    """
    headers: list[tuple[int, str, str]] = []
    for m in _ITEM_RE.finditer(text):
        num = m.group("num").upper()
        if num in _ITEM_LABELS:
            headers.append((m.start(), num, m.group(0)))

    # Group by item, take the latest (= body, not TOC) occurrence whose
    # following section is non-trivial.
    by_item: dict[str, list[tuple[int, int]]] = {}
    for i, (start, num, _hdr) in enumerate(headers):
        end = headers[i + 1][0] if i + 1 < len(headers) else len(text)
        by_item.setdefault(num, []).append((start, end))

    sections: dict[str, str] = {}
    paragraphs: dict[str, list[str]] = {}
    for num, ranges in by_item.items():
        # Pick the longest range — the body has more text than the TOC link.
        s, e = max(ranges, key=lambda r: r[1] - r[0])
        body = text[s:e].strip()
        if len(body) < 200:
            continue
        sections[num] = body
        paragraphs[num] = [
            p.strip() for p in body.split("\n\n") if len(p.strip()) > 40
        ]
    return sections, paragraphs


# Match the company name from common 10-K cover-page wording.
_COMPANY_NAME_RES = (
    re.compile(r"(?im)^\s*([A-Z][A-Za-z0-9 ,\.\-&]+(?:Inc\.|Co\.|Corporation|Holdings))\s*$"),
)
_FY_END_RE = re.compile(
    r"(?i)for\s+the\s+fiscal\s+year\s+ended\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})"
)


def parse_10k(html_path: Path | str) -> ParsedFiling:
    """Read a SEC 10-K HTML file and return structured sections + paragraphs."""
    html_path = Path(html_path)
    raw_html = html_path.read_text(encoding="utf-8", errors="replace")
    stripper = _TextStripper()
    stripper.feed(raw_html)
    text = _normalize_text(stripper.get_text())

    company_name = ""
    for r in _COMPANY_NAME_RES:
        m = r.search(text[:5000])
        if m:
            company_name = m.group(1).strip()
            break

    fy_match = _FY_END_RE.search(text[:8000])
    fy_end = fy_match.group(1) if fy_match else ""

    sections, paragraphs = _extract_sections(text)

    return ParsedFiling(
        company_name=company_name,
        fiscal_year_end=fy_end,
        sections=sections,
        paragraphs=paragraphs,
        raw_text=text,
        char_count=len(text),
    )


def section_label(item_num: str) -> str:
    return _ITEM_LABELS.get(item_num, f"Item {item_num}")
