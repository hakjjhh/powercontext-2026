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

"""CSV row Sources with stable identities and text-evidence projections."""

# ruff: noqa: TRY003, C901

from __future__ import annotations

import csv
import io
import json
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

CSV_ROW_SOURCE_TYPE = "csv-row"
CSV_ROW_SOURCE_VERSION = "1"


def _require_non_blank(value: str, field: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _require_trimmed(value: str, field: str) -> str:
    if not value or value != value.strip():
        raise ValueError(f"{field} must be a non-empty string without surrounding whitespace")
    return value


class CSVDocumentInput(BaseModel):
    """One complete offline CSV document to parse."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    primary_key: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _require_trimmed(value, "document_id")

    @field_validator("primary_key")
    @classmethod
    def validate_primary_key(cls, value: str) -> str:
        return _require_non_blank(value, "primary_key")


class CSVRowInput(BaseModel):
    """The complete, lossless representation of one CSV data row."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _require_trimmed(value, "document_id")

    @field_validator("primary_key")
    @classmethod
    def validate_primary_key(cls, value: str) -> str:
        return _require_non_blank(value, "primary_key")

    @field_validator("primary_key_value")
    @classmethod
    def validate_primary_key_value(cls, value: str) -> str:
        return _require_non_blank(value, "primary_key_value")

    @model_validator(mode="after")
    def validate_row(self) -> CSVRowInput:
        if not self.columns:
            raise ValueError("columns must not be empty")
        if len(self.columns) != len(self.values):
            raise ValueError("columns and values must have the same length")
        if any(not column or not column.strip() for column in self.columns):
            raise ValueError("columns must contain non-empty, non-blank names")
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("columns must be unique")
        try:
            primary_key_index = self.columns.index(self.primary_key)
        except ValueError as error:
            raise ValueError("primary_key must exactly match one column") from error
        if self.values[primary_key_index] != self.primary_key_value:
            raise ValueError("primary_key_value must match the primary-key column value")
        csv_row_source_id(self.document_id, self.primary_key_value)
        return self


class CSVRowSource(Source):
    """A captured Source representing one row of a CSV document."""

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @model_validator(mode="after")
    def validate_source(self) -> CSVRowSource:
        if self.materialization is not SourceMaterialization.CAPTURED:
            raise ValueError("CSV row Sources must be captured")
        expected_id = csv_row_source_id(self.document_id, self.primary_key_value)
        if self.source_id != expected_id or self.name != expected_id:
            raise ValueError("CSV row source identity must match document_id and primary_key_value")
        CSVRowInput(
            document_id=self.document_id,
            primary_key=self.primary_key,
            primary_key_value=self.primary_key_value,
            columns=self.columns,
            values=self.values,
        )
        return self


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Build the stable, collision-free identity for one CSV row."""

    _require_trimmed(document_id, "document_id")
    _require_non_blank(primary_key_value, "primary_key_value")
    source_id = f"csv:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"csv-row source_id must not exceed {MAX_SOURCE_ID_LENGTH} characters")
    return source_id


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Parse and atomically validate a complete UTF-8 CSV document."""

    try:
        text = value.content.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("CSV content must be valid UTF-8") from error

    try:
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        try:
            columns = tuple(next(reader))
        except StopIteration as error:
            raise ValueError("CSV document must contain a header") from error

        if not columns or any(not column or not column.strip() for column in columns):
            raise ValueError("CSV header names must be non-empty and non-blank")
        if len(set(columns)) != len(columns):
            raise ValueError("CSV header names must be unique")
        if value.primary_key not in columns:
            raise ValueError("primary_key must exactly match a CSV header")
        primary_key_index = columns.index(value.primary_key)

        rows: list[CSVRowInput] = []
        seen_primary_keys: set[str] = set()
        for record in reader:
            if not record:
                continue
            if len(record) != len(columns):
                raise ValueError("CSV record width must match the header width")
            primary_key_value = record[primary_key_index]
            if not primary_key_value.strip():
                raise ValueError("CSV primary-key values must be non-empty and non-blank")
            if primary_key_value in seen_primary_keys:
                raise ValueError("CSV primary-key values must be unique within a document")
            seen_primary_keys.add(primary_key_value)
            rows.append(
                CSVRowInput(
                    document_id=value.document_id,
                    primary_key=value.primary_key,
                    primary_key_value=primary_key_value,
                    columns=columns,
                    values=tuple(record),
                )
            )
    except csv.Error as error:
        raise ValueError("CSV document contains malformed quoting") from error

    return tuple(rows)


class _CSVRowAdapter:
    name = CSV_ROW_SOURCE_TYPE
    input_class = CSVRowInput
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        source_id = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class _CSVRowTextEvidenceProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_TYPE,
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


CSV_ROW_SOURCE_DEFINITION: SourceDefinition[CSVRowInput, CSVRowSource, CSVRowInput] = AdapterSourceDefinition(
    adapter=_CSVRowAdapter(),
    version=CSV_ROW_SOURCE_VERSION,
    projections=(_CSVRowTextEvidenceProjection(),),
)


__all__ = [
    "CSV_ROW_SOURCE_DEFINITION",
    "CSV_ROW_SOURCE_TYPE",
    "CSV_ROW_SOURCE_VERSION",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "csv_row_source_id",
    "parse_csv_document",
]
