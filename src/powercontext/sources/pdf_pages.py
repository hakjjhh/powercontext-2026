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

"""Page-level PDF Sources with stable identities, text evidence and offline retrieval."""

# ruff: noqa: TRY003, C901, BLE001

from __future__ import annotations

import hashlib
import io
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pypdf import PdfReader

from powercontext.builtin.artifacts.search import analyze_fts_query, analyze_text_with_spans
from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PDF_PAGE_SOURCE_TYPE = "pdf-page"
PDF_PAGE_SOURCE_VERSION = "1"

_SHA256_HEX = re.compile(r"\A[0-9a-f]{64}\Z")
_ID_SEPARATOR = "\u241f"
_MAX_QUERY_VARIANTS = 32
_MIN_CANDIDATE_POOL = 24
_BM25_K1 = 1.2
_BM25_B = 0.75
_PHRASE_BONUS = 1.5


def _require_non_blank(value: str, field: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _require_sha256(value: str, field: str) -> str:
    if not isinstance(value, str) or _SHA256_HEX.match(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


class PDFDocumentInput(BaseModel):
    """One complete original PDF document to parse into physical pages."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _require_non_blank(value, "document_id")

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: bytes) -> bytes:
        if not value:
            raise ValueError("content must be a non-empty PDF document")
        return value


class PDFPageInput(BaseModel):
    """The complete extractable text of one physical page of one document revision."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    document_sha256: str
    page_number: int = Field(ge=1)
    text: str

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _require_non_blank(value, "document_id")

    @field_validator("document_sha256")
    @classmethod
    def validate_document_sha256(cls, value: str) -> str:
        return _require_sha256(value, "document_sha256")


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Build the stable, collision-free identity for one physical page of one revision."""

    _require_non_blank(document_id, "document_id")
    _require_sha256(document_sha256, "document_sha256")
    if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1:
        raise ValueError("page_number must be a positive integer")
    source_id = f"pdf:{quote(document_id, safe='')}:{document_sha256}:{page_number}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        canonical = _ID_SEPARATOR.join((document_id, document_sha256, str(page_number)))
        source_id = f"pdf:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"
    return source_id


def _extract_page_text(page: Any) -> str:
    try:
        text = page.extract_text(extraction_mode="layout")
    except Exception:
        text = page.extract_text()
    if not text:
        return ""
    lines = [re.sub(r"[ \t]+", " ", line).rstrip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Parse every physical page of one complete PDF, atomically."""

    content = bytes(value.content)
    if not content:
        raise ValueError("PDF document must not be empty")
    document_sha256 = hashlib.sha256(content).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF document requires a non-empty password")
        pages = list(reader.pages)
        if not pages:
            raise ValueError("PDF document must contain at least one physical page")
        texts = [_extract_page_text(page) for page in pages]
    except ValueError:
        raise
    except Exception as error:
        raise ValueError(f"PDF document could not be parsed completely: {error}") from error
    return tuple(
        PDFPageInput(
            document_id=value.document_id,
            document_sha256=document_sha256,
            page_number=page_number,
            text=text,
        )
        for page_number, text in enumerate(texts, start=1)
    )


class PDFPageSource(Source):
    """A captured Source representing one physical page of one document revision."""

    source_id: str
    document_id: str
    document_sha256: str
    page_number: int = Field(ge=1)
    text: str

    @model_validator(mode="after")
    def validate_source(self) -> PDFPageSource:
        if self.materialization is not SourceMaterialization.CAPTURED:
            raise ValueError("PDF page Sources must be captured")
        expected_id = pdf_page_source_id(self.document_id, self.document_sha256, self.page_number)
        if self.source_id != expected_id or self.name != expected_id:
            raise ValueError("PDF page source identity must match document_id, revision digest and page_number")
        _require_sha256(self.document_sha256, "document_sha256")
        return self


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
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_DEFINITION: SourceDefinition[PDFPageInput, PDFPageSource, PDFPageInput] = AdapterSourceDefinition(
    adapter=_PDFPageAdapter(),
    version=PDF_PAGE_SOURCE_VERSION,
    projections=(_PDFPageTextEvidenceProjection(),),
)


@dataclass(frozen=True)
class _TermProfile:
    counts: Counter[str]
    bigrams: frozenset[str]
    length: int


@dataclass(slots=True)
class _Candidate:
    hit: Any
    profile: _TermProfile


def _tokenize(value: str) -> _TermProfile:
    counts: Counter[str] = Counter()
    bigrams: set[str] = set()
    previous: tuple[str, int] | None = None
    for term, start, end in analyze_text_with_spans(value):
        counts[term] += 1
        if previous is not None and previous[1] == start:
            bigrams.add(_ID_SEPARATOR.join((previous[0], term)))
        previous = (term, end)
    return _TermProfile(counts=counts, bigrams=frozenset(bigrams), length=sum(counts.values()))


def _question_terms(question: str) -> tuple[str, ...]:
    analyzed = analyze_fts_query(question)
    return tuple(term for term in analyzed.split() if term)


def _term_priority(term: str) -> tuple[int, int, int]:
    letters = sum(character.isalpha() for character in term)
    digits = sum(character.isdigit() for character in term)
    return (letters, len(term), digits)


def _query_variants(question: str) -> tuple[str, ...]:
    """Plan bounded offline FTS queries: the whole question, phrases, then selective terms."""

    terms = _question_terms(question)
    if not terms:
        trimmed = question.strip()
        return (trimmed,) if trimmed else ()
    distinct = list(dict.fromkeys(terms))
    variants: list[str] = [" ".join(distinct)]
    seen = {variants[0]}

    def add(value: str) -> None:
        if value not in seen:
            seen.add(value)
            variants.append(value)

    for size in (4, 2):
        start = 0
        while start + size <= len(distinct):
            add(" ".join(distinct[start : start + size]))
            start += size
    for term in sorted(set(distinct), key=_term_priority, reverse=True):
        if len(variants) >= _MAX_QUERY_VARIANTS:
            break
        add(term)
    return tuple(variants)


def _rank(question: str, candidates: list[_Candidate]) -> list[_Candidate]:
    total = len(candidates)
    question_terms = tuple(dict.fromkeys(_question_terms(question)))
    if not question_terms or not candidates:
        return candidates
    document_frequency: Counter[str] = Counter()
    for candidate in candidates:
        document_frequency.update(candidate.profile.counts.keys())
    average_length = sum(candidate.profile.length for candidate in candidates) / total

    def inverse_document_frequency(term: str) -> float:
        frequency = document_frequency[term]
        return math.log(1.0 + (total - frequency + 0.5) / (frequency + 0.5))

    term_weights = {term: inverse_document_frequency(term) for term in question_terms}
    question_bigrams = _tokenize(question).bigrams
    scored: list[tuple[float, int, _Candidate]] = []
    for order, candidate in enumerate(candidates):
        counts = candidate.profile.counts
        length = candidate.profile.length or 1
        normalized = _BM25_K1 * (1.0 - _BM25_B + _BM25_B * length / average_length)
        score = 0.0
        for term in question_terms:
            frequency = counts[term]
            if not frequency:
                continue
            score += term_weights[term] * (frequency * (_BM25_K1 + 1.0)) / (frequency + normalized)
        for bigram in question_bigrams.intersection(candidate.profile.bigrams):
            left, _, right = bigram.partition(_ID_SEPARATOR)
            score += _PHRASE_BONUS * min(term_weights.get(left, 0.0), term_weights.get(right, 0.0))
        scored.append((score, order, candidate))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [candidate for _score, _order, candidate in scored]


async def search_pdf_pages(memory: Any, question: str, limit: int = 10) -> tuple[Any, ...]:
    """Return up to ``limit`` distinct page citations for one natural-language question."""

    from powercontext.artifacts import MemoryCitation
    from powercontext.builtin.runtime import SearchMemoryRequest

    if not question or not question.strip():
        return ()
    try:
        ceiling = int(limit)
    except (TypeError, ValueError):
        return ()
    if ceiling <= 0:
        return ()
    variants = _query_variants(question)
    if not variants:
        return ()

    pool_size = max(ceiling, _MIN_CANDIDATE_POOL)
    collected: dict[str, _Candidate] = {}
    for variant in variants:
        try:
            page = await memory.search(SearchMemoryRequest(query=variant, limit=pool_size, mode="fts"))
        except Exception:
            continue
        for hit in getattr(page, "hits", ()):
            ref = hit.memory_ref
            key = _ID_SEPARATOR.join((ref.family, ref.artifact_id, str(ref.revision), hit.entry_id))
            if key in collected:
                continue
            profile = _tokenize(hit.text)
            if not profile.counts:
                continue
            collected[key] = _Candidate(hit=hit, profile=profile)
    ranked = _rank(question, list(collected.values()))
    return tuple(
        MemoryCitation(
            memory_ref=candidate.hit.memory_ref,
            entry_id=candidate.hit.entry_id,
            entry_version_id=candidate.hit.entry_version_id,
        )
        for candidate in ranked[:ceiling]
    )


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