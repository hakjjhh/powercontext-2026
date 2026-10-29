# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""PDF page Sources: complete per-page parsing plus evidence-page retrieval."""

# ruff: noqa: C901

from __future__ import annotations

import hashlib
import io
import math
import re
import unicodedata
import weakref
from collections import Counter
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from pypdf import PdfReader

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

if TYPE_CHECKING:
    from powercontext.artifacts.models import MemoryCitation

PDF_PAGE_SOURCE_TYPE = "pdf-page"
PDF_PAGE_SOURCE_VERSION = "1"

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
# "P&L" and "profit and loss" tokenize into single letters, so resolve them to the section
# name a filing actually uses before lexical analysis.
_INCOME_STATEMENT = re.compile(r"\bp\s*&\s*l\b|\bprofit\s+and\s+loss\b", re.IGNORECASE)
_MAX_PHRASE_WORDS = 3
_MAX_PHRASES = 16
_BM25_K1 = 1.2
_BM25_B = 0.75
_PHRASE_WEIGHT = 0.35
_COVERAGE_WEIGHT = 0.5
_HEADING_WEIGHT = 0.4
_HEADING_LINES = 15
_HEADING_MIN_TERMS = 2
_FTS_POOL = 60
_NUMBER_BONUS = 6.0
_STATEMENT_BONUS = 1.5
_STATEMENT_QUERY_BONUS = 6.0
_TOC_PENALTY = 0.6
_DENSE_TABLE_BONUS = 4.0
_BUSINESS_BONUS = 1.2
_SECTION_BONUS = 3.0
_SECTION_LEGAL_Q = re.compile(r"\blegal\b|\blitigation\b|\blawsuit", re.IGNORECASE)
_SECTION_BUSINESS_Q = re.compile(r"\bcustomer|\bcyclical|\bsegment|\bproduct|\bbusiness\b|\bservice", re.IGNORECASE)
_SECTION_MDA_Q = re.compile(r"\bliquidity|\bmargin|\btax\b|\bcapital|\bratio\b|\brevenue\b|\bgrowth\b", re.IGNORECASE)
_SECTION_STATEMENT_Q = re.compile(
    r"\bbalance\s+sheet\b|\b(?:income|cash\s+flow|operations)\s+statements?\b|\bp\s*&\s*l\b|"
    r"\b(?:quick|current)\s+ratio\b|\bgross\s+margin\b",
    re.IGNORECASE,
)
_TOC_SCAN_PAGES = 4
_TOC_ITEM = re.compile(r"\bitem\s+([0-9]{1,2}[a-c]?)\b", re.IGNORECASE)
_TOC_PAGE_NUMBER = re.compile(r"(?<![\d-])(\d{1,3})(?![\d-])")
_TOC_ITEM_CODES = frozenset(
    {"1", "1a", "1b", "2", "3", "4", "5", "6", "7", "7a", "8", "9", "9a", "9b", "9c"}
    | {str(number) for number in range(10, 17)}
)
_NEIGHBOR_WEIGHT = 0.2
_BUSINESS_QUESTION = re.compile(r"\bcustomer|\bcyclical|\bsegment|\bproduct|\bbusiness\b|\bservice", re.IGNORECASE)
_BUSINESS_MARKERS = frozenset({"customer","customers","segment","segments","business","product","products","cyclical","backlog"})
_NUMERIC_QUESTION = re.compile(r"[0-9%$]|\bratio\b|\bpercent|\bchange\b|\bhow much\b|\bhow many\b|\bcost\b|\bamount\b", re.IGNORECASE)
_STATEMENT_TITLES = (
    "consolidated balance sheet",
    "consolidated statement of earnings",
    "consolidated statements of earnings",
    "consolidated statement of income",
    "consolidated statements of income",
    "consolidated statement of cash flows",
    "consolidated statements of cash flows",
    "consolidated statement of operations",
    "consolidated statements of operations",
    "notes to consolidated financial statements",
    "consolidated statement of financial position",
    "consolidated statements of financial position",
    "consolidated statements of changes in equity",
    "consolidated statements of comprehensive income",
    "consolidated statement of comprehensive income",
)

# Generic finance and legal vocabulary, so a question phrased with everyday words still
# reaches the section that uses the filing's own wording. Added terms never remove the
# original ones; they only widen recall.
_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "battle": ("proceeding", "litigation", "lawsuit", "dispute"),
    "battles": ("proceedings", "litigation", "lawsuits", "disputes"),
    "legal": ("litigation", "proceedings", "lawsuit", "disputes"),
    "geographic": ("region", "country", "market"),
    "geographies": ("regions", "geographic", "countries", "markets", "worldwide"),
    "geography": ("regions", "geographic", "countries", "markets", "worldwide"),
    "region": ("geographic", "country"),
    "regions": ("geographic", "countries"),
    "restructuring": ("impairment", "charges"),
    "capex": ("capital", "expenditures"),
    "ebitda": ("operating", "profit", "depreciation", "amortization"),
    "margin": ("profit", "income"),
    "revenue": ("sales", "net"),
    "revenues": ("sales", "net"),
    "debt": ("borrowings", "obligations"),
    "borrowings": ("debt", "commercial", "paper"),
    "employees": ("headcount", "personnel"),
    "dividend": ("dividends", "payout"),
    "dividends": ("payout", "distribution"),
    "liquidity": ("cash", "working", "capital"),
    "lawsuit": ("litigation", "proceedings"),
    "lawsuits": ("litigation", "proceedings"),
    "dispute": ("litigation", "proceedings"),
    "disputes": ("litigation", "proceedings"),
    "tariff": ("duty", "customs"),
    "tariffs": ("duties", "customs"),
    "pension": ("retirement", "benefit"),
    "shareholder": ("stockholders", "equity"),
    "shareholders": ("stockholders", "equity"),
    "headcount": ("employees",),
}

_QUERY_FUNCTION_WORDS = frozenset(
    """
    a an the and or for of to in on at by from with as is are was were be been being
    what which who how why when where do does did can could should would i we you it its
    our your this that these those please
    """.split()
)


def _require_trimmed(value: str, field: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    if value != value.strip():
        raise ValueError(f"{field} must not contain surrounding whitespace")
    return value


def _require_sha256(value: str) -> str:
    if not isinstance(value, str) or not _SHA256_HEX.fullmatch(value):
        raise ValueError("document_sha256 must be a lowercase hexadecimal SHA-256 digest")
    return value


class PDFDocumentInput(BaseModel):
    """One complete original PDF document to parse."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _require_trimmed(value, "document_id")

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: bytes) -> bytes:
        if not value:
            raise ValueError("content must not be empty")
        return value


class PDFPageInput(BaseModel):
    """The complete captured value of one physical PDF page."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    document_sha256: str
    page_number: int
    text: str

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _require_trimmed(value, "document_id")

    @field_validator("document_sha256")
    @classmethod
    def validate_document_sha256(cls, value: str) -> str:
        return _require_sha256(value)

    @field_validator("page_number")
    @classmethod
    def validate_page_number(cls, value: int) -> int:
        if value < 1:
            raise ValueError("page_number must be at least 1")
        return value


class PDFPageSource(Source):
    """A captured Source representing one physical page of one PDF document."""

    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str

    @model_validator(mode="after")
    def validate_source(self) -> PDFPageSource:
        if self.materialization is not SourceMaterialization.CAPTURED:
            raise ValueError("PDF page Sources must be captured")
        expected_id = pdf_page_source_id(self.document_id, self.document_sha256, self.page_number)
        if self.source_id != expected_id or self.name != expected_id:
            raise ValueError("PDF page source identity must match document, digest and page number")
        PDFPageInput(
            document_id=self.document_id,
            document_sha256=self.document_sha256,
            page_number=self.page_number,
            text=self.text,
        )
        return self


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Build the stable, collision-resistant identity for one physical page."""

    _require_trimmed(document_id, "document_id")
    _require_sha256(document_sha256)
    if page_number < 1:
        raise ValueError("page_number must be at least 1")
    readable = f"pdf:{quote(document_id, safe='')}:{document_sha256}:{page_number}"
    if len(readable) <= MAX_SOURCE_ID_LENGTH:
        return readable
    digest = hashlib.sha256(
        f"{document_id}\x00{document_sha256}\x00{page_number}".encode("utf-8")
    ).hexdigest()
    return f"pdf:{digest}"


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Parse every physical page of an original PDF, or fail atomically."""

    content = value.content
    document_sha256 = hashlib.sha256(content).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted and int(reader.decrypt("")) == 0:
            raise ValueError("PDF requires a password that was not supplied")
        pages = tuple(reader.pages)
    except ValueError:
        raise
    except Exception as error:  # noqa: BLE001 - pypdf raises many concrete error types
        raise ValueError("PDF document could not be parsed") from error
    if not pages:
        raise ValueError("PDF document must contain at least one page")

    parsed: list[PDFPageInput] = []
    for index, page in enumerate(pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as error:  # noqa: BLE001 - one broken page invalidates the document
            raise ValueError("PDF document could not be fully parsed") from error
        parsed.append(
            PDFPageInput(
                document_id=value.document_id,
                document_sha256=document_sha256,
                page_number=index,
                text=text.strip(),
            )
        )
    return tuple(parsed)


class _PDFPageAdapter:
    name = PDF_PAGE_SOURCE_TYPE
    input_class = PDFPageInput
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        source_id = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class _PDFPageTextEvidenceProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_TYPE,
            source_id=source.source_id,
            content=source.document_id + "\n" + source.text,
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_DEFINITION: SourceDefinition[PDFPageInput, PDFPageSource, PDFPageInput] = (
    AdapterSourceDefinition(
        adapter=_PDFPageAdapter(),
        version=PDF_PAGE_SOURCE_VERSION,
        projections=(_PDFPageTextEvidenceProjection(),),
    )
)


def _is_cjk(character: str) -> bool:
    point = ord(character)
    return (
        0x3400 <= point <= 0x4DBF
        or 0x4E00 <= point <= 0x9FFF
        or 0xF900 <= point <= 0xFAFF
        or 0x20000 <= point <= 0x2FA1F
    )


def _tokenize(value: str) -> list[str]:
    """Lowercase alphanumeric terms, with CJK characters expanded to unigrams and bigrams."""

    normalized = unicodedata.normalize("NFC", value).casefold()
    terms: list[str] = []
    word: list[str] = []
    cjk: list[str] = []

    def flush_word() -> None:
        if word:
            terms.append("".join(word))
            word.clear()

    def flush_cjk() -> None:
        if not cjk:
            return
        terms.extend(f"u_{ord(character):x}" for character in cjk)
        terms.extend(f"b_{ord(left):x}_{ord(right):x}" for left, right in zip(cjk, cjk[1:]))
        cjk.clear()

    for character in normalized:
        if _is_cjk(character):
            flush_word()
            cjk.append(character)
        elif character.isalnum():
            flush_cjk()
            word.append(character)
        else:
            flush_word()
            flush_cjk()
    flush_word()
    flush_cjk()
    return terms


def _normalize_question(question: str) -> str:
    return _INCOME_STATEMENT.sub("income statement", question)


def _query_terms(question: str) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for term in _tokenize(_normalize_question(question)):
        if term in _QUERY_FUNCTION_WORDS:
            continue
        seen.setdefault(term, None)
    for term in tuple(seen):
        for expansion in _EXPANSIONS.get(term, ()):
            seen.setdefault(expansion, None)
    return tuple(seen)


def _query_phrases(question: str) -> tuple[tuple[str, ...], ...]:
    tokens = _tokenize(_normalize_question(question))
    phrases: dict[tuple[str, ...], None] = {}
    for size in range(2, _MAX_PHRASE_WORDS + 1):
        for start in range(0, len(tokens) - size + 1):
            window = tuple(tokens[start : start + size])
            if not any(term not in _QUERY_FUNCTION_WORDS for term in window):
                continue
            phrases.setdefault(window, None)
            if len(phrases) >= _MAX_PHRASES:
                return tuple(phrases)
    return tuple(phrases)


def _toc_entries(pages: list[tuple[int, str]]) -> tuple[dict[str, int], frozenset[int]]:
    """Extract Item-to-page entries from the first few pages of a filing."""

    candidates: list[tuple[int, str]] = []
    for page_number, text in pages[:_TOC_SCAN_PAGES]:
        if len(_TOC_ITEM.findall(text)) >= 5:
            candidates.append((page_number, text))
    entries: dict[str, int] = {}
    for index, (_page_number, text) in enumerate(candidates):
        if index == 0:
            part_one = re.search(r"\bpart\s+i\b", text, re.IGNORECASE)
            if part_one:
                text = text[part_one.start() :]
        matches = list(_TOC_ITEM.finditer(text))
        for match_index, match in enumerate(matches):
            item = match.group(1).casefold()
            if item not in _TOC_ITEM_CODES:
                continue
            end = matches[match_index + 1].start() if match_index + 1 < len(matches) else len(text)
            segment = re.sub(r"\b10-k\b", "", text[match.end() : end], flags=re.IGNORECASE)
            page_match = _TOC_PAGE_NUMBER.search(segment)
            if page_match:
                entries.setdefault(item, int(page_match.group(1)))
    return entries, frozenset(page_number for page_number, _text in candidates)


def _toc_section_ranges(pages: list[tuple[int, str]]) -> dict[str, tuple[int, int]]:
    """Map each Form 10-K Item to its physical page interval."""

    entries, toc_pages = _toc_entries(pages)
    if len(entries) < 5 or not pages:
        return {}
    occurrences: dict[str, list[int]] = {}
    for item in entries:
        item_pattern = re.compile(rf"\bitem\s+{re.escape(item)}\b", re.IGNORECASE)
        occurrences[item] = [
            page_number
            for page_number, text in pages
            if page_number not in toc_pages and item_pattern.search(text)
        ]
    votes: Counter[int] = Counter()
    for item, printed_page in entries.items():
        for physical_page in occurrences[item]:
            offset = physical_page - printed_page
            if -10 <= offset <= 30:
                votes[offset] += 1
    if not votes:
        return {}
    offset, support = max(votes.items(), key=lambda item: (item[1], -abs(item[0])))
    if support < max(2, len(entries) // 4):
        return {}

    last_page = pages[-1][0]
    starts = {
        item: printed_page + offset
        for item, printed_page in entries.items()
        if 1 <= printed_page + offset <= last_page
    }
    ranges: dict[str, tuple[int, int]] = {}
    for item, start in starts.items():
        later_starts = [candidate for candidate in starts.values() if candidate > start]
        end = min(later_starts) - 1 if later_starts else last_page
        ranges[item] = (start, end)
    return ranges


class _PDFPageIndex:
    """A per-scope lexical index over every page stored in one Memory."""

    def __init__(
        self,
        documents: list[
            tuple[Any, str, Counter[str], int, str, frozenset[str], str, float, bool, int, frozenset[str]]
        ],
    ) -> None:
        self._documents = documents
        self._count = len(documents)
        self._average_length = (
            sum(document[3] for document in documents) / self._count if self._count else 0.0
        )
        frequencies: Counter[str] = Counter()
        for document in documents:
            frequencies.update(document[2].keys())
        self._document_frequency = frequencies
        self._document_terms = {
            document[6]: frozenset(_tokenize(document[6])) for document in documents
        }

    def __bool__(self) -> bool:
        return self._count > 0

    def _idf(self, term: str) -> float:
        frequency = self._document_frequency.get(term, 0)
        return math.log(1.0 + (self._count - frequency + 0.5) / (frequency + 0.5))

    @staticmethod
    def _wanted_sections(question: str) -> frozenset[str]:
        wanted: set[str] = set()
        if _SECTION_LEGAL_Q.search(question):
            wanted.add("3")
        elif _SECTION_MDA_Q.search(question):
            wanted.update(("7", "7a"))
        elif _SECTION_BUSINESS_Q.search(question):
            wanted.update(("1", "1a"))
        if _SECTION_STATEMENT_Q.search(question):
            wanted.add("8")
        return frozenset(wanted)
    def _scoped_document(self, question: str) -> str | None:
        """Return the single document the question names, if exactly one dominates."""

        if len(self._document_terms) < 2:
            return None
        asked = set(_tokenize(question))
        overlap = {
            document: len(asked & tokens) for document, tokens in self._document_terms.items()
        }
        ranked = sorted(overlap.items(), key=lambda item: item[1], reverse=True)
        if ranked[0][1] < 1 or (len(ranked) > 1 and ranked[1][1] == ranked[0][1]):
            return None
        return ranked[0][0]

    def search(self, question: str, limit: int) -> list[Any]:
        terms = _query_terms(question)
        phrases = _query_phrases(question)
        term_ids = {term: self._idf(term) for term in terms}
        weights = {term: value for term, value in term_ids.items() if value > 0.0}
        phrase_weights = [
            (phrase, _PHRASE_WEIGHT * sum(self._idf(term) for term in phrase))
            for phrase in phrases
        ]
        scored: list[tuple[float, str, Any]] = []
        average_length = self._average_length or 1.0
        scope = self._scoped_document(question)
        numeric = _NUMERIC_QUESTION.search(question) is not None
        business = _BUSINESS_QUESTION.search(question) is not None
        wanted = self._wanted_sections(question)
        statement_query = _SECTION_STATEMENT_Q.search(question) is not None
        for (
            citation,
            key,
            counts,
            length,
            joined,
            head_terms,
            document,
            density,
            has_statement,
            toc_hits,
            sections,
        ) in self._documents:
            if scope is not None and document != scope:
                continue
            score = 0.0
            matched = 0
            for term, weight in weights.items():
                frequency = counts.get(term, 0)
                if not frequency:
                    continue
                matched += 1
                denominator = frequency + _BM25_K1 * (1.0 - _BM25_B + _BM25_B * length / average_length)
                score += weight * frequency * (_BM25_K1 + 1.0) / denominator
            if not matched:
                continue
            if terms:
                score += _COVERAGE_WEIGHT * (matched / len(terms)) * sum(term_ids.values())
            for phrase, weight in phrase_weights:
                if weight > 0.0 and " ".join(phrase) in joined:
                    score += weight
            head_matched = [term for term in weights if term in head_terms]
            if len(head_matched) >= _HEADING_MIN_TERMS:
                score += _HEADING_WEIGHT * sum(weights[term] for term in head_matched)
            if wanted and not wanted.isdisjoint(sections):
                score += _SECTION_BONUS
            if business and not numeric:
                score += _BUSINESS_BONUS * len(_BUSINESS_MARKERS & counts.keys())
            if numeric:
                score += _NUMBER_BONUS * density
                if density >= 0.22:
                    score += _DENSE_TABLE_BONUS
            if has_statement:
                score += _STATEMENT_BONUS
                if statement_query:
                    score += _STATEMENT_QUERY_BONUS
            score -= _TOC_PENALTY * min(toc_hits, 4)
            scored.append((score, key, citation))
        scored = _smooth_neighbors(scored)
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [(score, citation) for score, _key, citation in scored[:limit]]


_INDEX_CACHE: dict[int, tuple[Any, _PDFPageIndex]] = {}


def _layout_key(entry: Any) -> str:
    sources = getattr(entry.entry, "sources", ()) or ()
    identifiers = tuple(sorted(reference.source_id for reference in sources))
    return "\x00".join(identifiers) or entry.entry.entry_id


def _split_key(key: str) -> tuple[str, int | None]:
    head, _, tail = key.rpartition(":")
    return head, int(tail) if tail.isdigit() else None


def _smooth_neighbors(scored: list[tuple[float, str, Any]]) -> list[tuple[float, str, Any]]:
    """A table or section often crosses a page boundary, so lift pages next to a strong one."""

    by_page: dict[tuple[str, int], tuple[float, str, Any]] = {}
    loose: list[tuple[float, str, Any]] = []
    for score, key, citation in scored:
        document, page = _split_key(key)
        if page is None:
            loose.append((score, key, citation))
        else:
            by_page[(document, page)] = (score, key, citation)
    smoothed: list[tuple[float, str, Any]] = []
    for (document, page), (score, key, citation) in by_page.items():
        neighbours = [
            by_page[(document, page + step)][0]
            for step in (-1, 1)
            if (document, page + step) in by_page
        ]
        boosted = score + _NEIGHBOR_WEIGHT * max(neighbours) if neighbours else score
        smoothed.append((boosted, key, citation))
    return smoothed + loose


def _build_index(entries: tuple[Any, ...]) -> _PDFPageIndex:
    raw_documents: list[tuple[Any, str, list[str], str, frozenset[str], str, int | None]] = []
    page_texts: dict[str, list[tuple[int, str]]] = {}
    seen: set[str] = set()
    for entry in entries:
        if getattr(entry, "state", "active") != "active":
            continue
        text = entry.entry.text
        if not text or not text.strip():
            continue
        key = _layout_key(entry)
        if key in seen:
            continue
        seen.add(key)
        tokens = _tokenize(text)
        if not tokens:
            continue
        document = text.splitlines()[0].strip() if text.splitlines() else ""
        _source_key, page_number = _split_key(key)
        head_lines = [line for line in text.splitlines()[1:] if line.strip()][:_HEADING_LINES]
        head_terms = frozenset(_tokenize(" ".join(head_lines)))
        joined = " " + " ".join(tokens) + " "
        raw_documents.append((entry.citation, key, tokens, joined, head_terms, document, page_number))
        if page_number is not None:
            page_texts.setdefault(document, []).append((page_number, text))

    section_ranges = {
        document: _toc_section_ranges(sorted(pages)) for document, pages in page_texts.items()
    }
    documents: list[
        tuple[Any, str, Counter[str], int, str, frozenset[str], str, float, bool, int, frozenset[str]]
    ] = []
    for citation, key, tokens, joined, head_terms, document, page_number in raw_documents:
        ranges = section_ranges.get(document, {})
        sections = frozenset(
            item
            for item, (start, end) in ranges.items()
            if page_number is not None and start <= page_number <= end
        )
        documents.append(
            (
                citation,
                key,
                Counter(tokens),
                len(tokens),
                joined,
                head_terms,
                document,
                sum(1 for token in tokens if token.isdigit()) / len(tokens),
                any(title in joined for title in _STATEMENT_TITLES),
                joined.count(" item "),
                sections,
            )
        )
    return _PDFPageIndex(documents)

async def _page_index(memory: Any) -> _PDFPageIndex:
    cached = _INDEX_CACHE.get(id(memory))
    if cached is not None and cached[0]() is memory:
        return cached[1]
    try:
        page = await memory.list()
    except Exception:  # noqa: BLE001 - a backend without entry listing must still answer
        return _PDFPageIndex([])
    index = _build_index(tuple(page.entries))
    try:
        _INDEX_CACHE[id(memory)] = (weakref.ref(memory), index)
    except TypeError:  # pragma: no cover - only if a Memory implementation is not weak-referenceable
        pass
    return index


async def search_pdf_pages(
    memory: Any, question: str, limit: int = 10
) -> tuple[MemoryCitation, ...]:
    """Return exact citations for the pages that best answer one original question."""

    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    index = await _page_index(memory)
    if index:
        ranked = index.search(question, limit)
        return tuple(citation for _score, citation in ranked)
    return await _search_via_fts(memory, question, limit)


async def _search_via_fts(memory: Any, question: str, limit: int) -> tuple[MemoryCitation, ...]:
    """Fallback for runtimes that cannot enumerate entries: rank the Memory FTS hits."""

    from powercontext.artifacts.models import MemoryCitation
    from powercontext.builtin.runtime.models import SearchMemoryRequest

    page = await memory.search(
        SearchMemoryRequest(query=question, limit=max(limit, _FTS_POOL), mode="fts")
    )
    citations = tuple(
        MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        for hit in getattr(page, "hits", ())
    )
    return citations[:limit]


__all__ = [
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDF_PAGE_SOURCE_TYPE",
    "PDF_PAGE_SOURCE_VERSION",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "parse_pdf_document",
    "pdf_page_source_id",
    "search_pdf_pages",
]
