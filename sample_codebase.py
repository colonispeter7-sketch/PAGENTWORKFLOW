"""Bounded reads of the bundled fictional fixture; never accepts a root directory."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
ALLOWED_FILES = ("index.html", "styles.css", "app.js")
MAX_FILE_BYTES = 16 * 1024
MAX_TOTAL_BYTES = 32 * 1024


class SampleAccessError(RuntimeError):
    """A sample read was denied; no partial batch is returned."""


class SourceCitationError(RuntimeError):
    """A source review has missing or invalid file/line references."""


@dataclass(frozen=True)
class SourceFile:
    path: str
    sha256: str
    line_count: int


class SampleReader:
    """One batch per workflow, with an exact filename allowlist and safe opens."""

    def __init__(self):
        self.sources: tuple[SourceFile, ...] = ()
        self._attempted = False

    def read(self, paths: list[str]) -> str:
        if self._attempted:
            raise SampleAccessError("Only one sample-file batch is allowed per workflow.")
        self._attempted = True
        # Validate the entire request before opening any directories or files.
        # Exact names reject absolute paths, traversal, hidden files and encodings.
        if (not isinstance(paths, list) or not 1 <= len(paths) <= len(ALLOWED_FILES)
                or any(not isinstance(path, str) or path not in ALLOWED_FILES for path in paths)
                or len(set(paths)) != len(paths)):
            raise SampleAccessError("Request one to three unique allowlisted sample filenames.")
        if (not all(hasattr(os, flag) for flag in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK"))
                or os.open not in os.supports_dir_fd):
            raise SampleAccessError("Secure sample reads require POSIX directory-relative opens.")

        files = []
        sources = []
        total = 0
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            with ExitStack() as stack:
                anchor = os.open(REPO_ROOT, directory_flags)
                stack.callback(os.close, anchor)
                # Anchor every directory component to a descriptor. Checking only
                # a resolved path would leave a symlink-swap race before open().
                for component in ("examples", "sample_frontend"):
                    anchor = os.open(component, directory_flags, dir_fd=anchor)
                    stack.callback(os.close, anchor)
                for path in paths:
                    fd = os.open(
                        path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=anchor
                    )
                    stream = stack.enter_context(os.fdopen(fd, "rb"))
                    before = os.fstat(fd)
                    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                        raise SampleAccessError("Sample sources must be regular files with one hard link.")
                    if before.st_size > MAX_FILE_BYTES:
                        raise SampleAccessError("A sample file exceeds the 16 KiB limit.")
                    data = stream.read(MAX_FILE_BYTES + 1)
                    after = os.fstat(fd)
                    if (len(data) > MAX_FILE_BYTES or after.st_size != len(data)
                            or before.st_size != after.st_size
                            or before.st_mtime_ns != after.st_mtime_ns
                            or before.st_ctime_ns != after.st_ctime_ns
                            or after.st_nlink != 1):
                        raise SampleAccessError("A sample file changed during the bounded read.")
                    total += len(data)
                    if total > MAX_TOTAL_BYTES:
                        raise SampleAccessError("The sample batch exceeds the 32 KiB limit.")
                    text = data.decode("utf-8")
                    if not text.strip() or "\0" in text:
                        raise SampleAccessError("Sample sources must be nonempty UTF-8 text.")
                    lines = text.splitlines()
                    source = SourceFile(path, hashlib.sha256(data).hexdigest(), len(lines))
                    sources.append(source)
                    files.append({**asdict(source), "numbered_source": "\n".join(
                        f"{number}: {line}" for number, line in enumerate(lines, 1)
                    )})
        except (OSError, UnicodeError):
            # Do not include local absolute paths or file contents in failures.
            raise SampleAccessError("The fictional fixture is unavailable or unsafe to read.") from None
        self.sources = tuple(sources)
        return json.dumps({"kind": "untrusted_fictional_source", "files": files})


def require_citations(text: str, sources: tuple[SourceFile, ...]) -> None:
    """Check bracketed file/line references, not the semantic truth of findings."""
    available = {source.path: source.line_count for source in sources}
    count = 0
    for group in re.findall(r"\[([^\[\]\n]+)\]", text):
        if ":" not in group:
            continue
        path = None
        # Accept [index.html:12, app.js:2-5] and [index.html:13, 29] as
        # well as canonical single references. Never silently skip a bad group.
        for part in re.split(r"[,;]", group):
            part = part.strip()
            if ":" in part:
                path, part = part.split(":", 1)
            lines = re.fullmatch(r"([0-9]{1,8})(?:-([0-9]{1,8}))?", part)
            if path not in available or lines is None:
                raise SourceCitationError("A source citation has an unknown file or invalid syntax.")
            start, end = int(lines[1]), int(lines[2] or lines[1])
            if not 1 <= start <= end <= available[path]:
                raise SourceCitationError("A citation is outside the files and lines actually read.")
            count += 1
    if not count:
        raise SourceCitationError("Source reviews require citations such as [index.html:9-11].")
