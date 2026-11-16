"""Versioned fictional knowledge and a bounded, non-applying unified diff parser."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sample_codebase import SourceFile, require_citations

ROOT = Path(__file__).resolve().parent.parent
MAX_DIFF_BYTES = 32 * 1024
MAX_KNOWLEDGE_BYTES = 24 * 1024
MAX_DIFF_LINES = 800
MAX_FILES = 8


class OversightError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @field_validator("*")
    @classmethod
    def nonblank_strings(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("Text must not be blank.")
        return value


class Source(Record):
    document: str = Field(min_length=1, max_length=160)
    revision: str = Field(min_length=1, max_length=80)
    locator: str = Field(min_length=1, max_length=160)


class Approval(Record):
    by: str = Field(min_length=1, max_length=80)
    source_revision: str = Field(min_length=1, max_length=80)


class KnowledgeEntry(Record):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,47}$")
    revision: int = Field(ge=1, le=1_000_000)
    kind: Literal["vision", "module_objective", "ui_principle", "architecture_principle",
                  "priority", "decision", "limitation", "exclusion"]
    status: Literal["approved", "assumption", "suggestion", "unverified"]
    text: str = Field(min_length=1, max_length=1500)
    source: Source
    revised_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    approval: Approval | None
    modules: list[str] = Field(max_length=12)
    # Exact repository-relative paths, metadata only. Never opened.
    paths: list[str] = Field(max_length=12)
    dependencies: list[str] = Field(max_length=12)

    @field_validator("revised_on")
    @classmethod
    def valid_date(cls, value):
        date.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def check_authority(self):
        if (self.status == "approved") != (self.approval is not None):
            raise ValueError("Only approved knowledge must carry explicit approval provenance.")
        if self.approval and self.approval.source_revision != self.source.revision:
            raise ValueError("Approval must refer to the current source revision.")
        for path in self.paths:
            safe_path(path)
        if any(not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", name)
               for name in self.modules + self.dependencies):
            raise ValueError("Invalid module identifier.")
        return self


class Knowledge(Record):
    schema_version: Literal[1]
    project: Literal["fictional-lantern-board"]
    revision: int = Field(ge=1)
    fictional: Literal[True]
    entries: list[KnowledgeEntry] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def check_entries(self):
        if len({entry.id for entry in self.entries}) != len(self.entries):
            raise ValueError("Duplicate knowledge identifiers.")
        if not any(entry.status == "approved" for entry in self.entries):
            raise ValueError("At least one approved objective is required.")
        modules = {module for entry in self.entries for module in entry.modules}
        if any(set(entry.dependencies) - modules for entry in self.entries):
            raise ValueError("Unknown module dependency.")
        return self

    @property
    def sha256(self) -> str:
        data = json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(data.encode()).hexdigest()


def safe_path(path: str) -> str:
    if (len(path) > 160 or len(path.split("/")) > 8
            or any(not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", part)
                   or part in (".", "..") for part in path.split("/"))):
        raise OversightError("diff_input", "Only simple relative paths are supported; paths are never opened.")
    return path


def load_knowledge() -> Knowledge:
    """Load only the tracked fictional knowledge fixture, never a caller path."""
    try:
        with (ROOT / "knowledge" / "fictional_lantern.json").open("rb") as stream:
            data = stream.read(MAX_KNOWLEDGE_BYTES + 1)
        if len(data) > MAX_KNOWLEDGE_BYTES:
            raise ValueError("Too large")
        return Knowledge.model_validate_json(data)
    except (OSError, ValueError, OversightError):
        raise OversightError("knowledge", "The versioned fictional knowledge is missing or invalid.") from None


class DiffLine(Record):
    line: int
    path: str
    kind: Literal["added", "removed", "context"]
    old_line: int | None
    new_line: int | None
    text: str


class Change(Record):
    path: str
    operation: Literal["modified", "added", "deleted"]
    added: int
    removed: int
    citations: list[str]


class DiffEvidence(Record):
    sha256: str
    line_count: int
    changes: list[Change]
    lines: list[DiffLine]

    def validate_citations(self, text: str) -> None:
        """Reuse the existing source citation check, then restrict to hunk data."""
        try:
            require_citations(text, (SourceFile("diff.patch", self.sha256, self.line_count),))
            allowed = {line.line for line in self.lines}
            for group in re.findall(r"\[([^\[\]\n]+)\]", text):
                if ":" not in group:
                    continue
                # This new workflow deliberately accepts single-file citations only.
                match = re.fullmatch(r"diff\.patch:(\d{1,3})(?:-(\d{1,3}))?", group)
                if not match or not set(range(int(match[1]), int(match[2] or match[1]) + 1)) <= allowed:
                    raise ValueError("Not hunk evidence")
        except (RuntimeError, ValueError):
            raise OversightError("citation", "A diff citation is missing or does not identify supplied hunk evidence.") from None


def parse_diff(text: str) -> DiffEvidence:
    """Accept text-only Git unified diffs. Never apply, fetch, or open named paths."""
    def invalid():
        raise OversightError("diff_input", "Invalid or unsupported unified diff; use ordinary text hunks with matching counts.")

    if not isinstance(text, str):
        invalid()
    try:
        data = text.encode("utf-8")
    except UnicodeError:
        invalid()
    raw = text.split("\n")
    if raw[-1] == "":
        raw.pop()
    if (not data or len(data) > MAX_DIFF_BYTES or len(raw) > MAX_DIFF_LINES
            or re.search(r"[\x00-\x08\x0b-\x1f\x7f]", text)):
        invalid()
    lines: list[DiffLine] = []
    changes: list[Change] = []
    index = 0
    while index < len(raw):
        header = re.fullmatch(r"diff --git a/(\S+) b/(\S+)", raw[index])
        if not header or header[1] != header[2]:
            invalid()
        path = safe_path(header[1])
        if path in {change.path for change in changes} or len(changes) >= MAX_FILES:
            invalid()
        index += 1
        operation = "modified"
        if index < len(raw) and raw[index] in ("new file mode 100644", "deleted file mode 100644"):
            operation = "added" if raw[index].startswith("new") else "deleted"
            index += 1
        if index < len(raw) and re.fullmatch(r"index [a-f0-9]+\.\.[a-f0-9]+(?: 100644)?", raw[index]):
            index += 1
        expected_old = "/dev/null" if operation == "added" else f"a/{path}"
        expected_new = "/dev/null" if operation == "deleted" else f"b/{path}"
        if raw[index:index + 2] != [f"--- {expected_old}", f"+++ {expected_new}"]:
            invalid()
        index += 2
        file_lines: list[DiffLine] = []
        old_end = new_end = 0
        while index < len(raw) and raw[index].startswith("@@"):
            hunk = re.fullmatch(r"@@ -(\d{1,7})(?:,(\d{1,7}))? \+(\d{1,7})(?:,(\d{1,7}))? @@(?: .*)?", raw[index])
            if not hunk:
                invalid()
            old, old_count = int(hunk[1]), int(hunk[2] or 1)
            new, new_count = int(hunk[3]), int(hunk[4] or 1)
            if (old < old_end or new < new_end or not (old_count or new_count)
                    or (old_count and old == 0) or (new_count and new == 0)
                    or (operation == "added" and (old != 0 or old_count != 0))
                    or (operation == "deleted" and (new != 0 or new_count != 0))):
                invalid()
            old_end, new_end = old + old_count, new + new_count
            index += 1
            remaining_old, remaining_new = old_count, new_count
            while remaining_old or remaining_new:
                if index >= len(raw) or not raw[index] or raw[index][0] not in " +-":
                    invalid()
                prefix, value = raw[index][0], raw[index][1:]
                consumes_old, consumes_new = prefix != "+", prefix != "-"
                remaining_old -= int(consumes_old)
                remaining_new -= int(consumes_new)
                if remaining_old < 0 or remaining_new < 0:
                    invalid()
                file_lines.append(DiffLine(
                    line=index + 1, path=path,
                    kind={"+": "added", "-": "removed", " ": "context"}[prefix],
                    old_line=old if consumes_old else None,
                    new_line=new if consumes_new else None, text=value,
                ))
                old += int(consumes_old)
                new += int(consumes_new)
                index += 1
                if index < len(raw) and raw[index] == "\\ No newline at end of file":
                    index += 1
        changed = [line for line in file_lines if line.kind != "context"]
        if not changed:
            invalid()
        changes.append(Change(path=path, operation=operation,
                              added=sum(line.kind == "added" for line in changed),
                              removed=sum(line.kind == "removed" for line in changed),
                              citations=[f"diff.patch:{line.line}" for line in changed]))
        lines.extend(file_lines)
    return DiffEvidence(sha256=hashlib.sha256(data).hexdigest(), line_count=len(raw),
                        changes=changes, lines=lines)
