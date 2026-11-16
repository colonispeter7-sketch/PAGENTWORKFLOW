# Task #4: controlled, read-only sample codebase

The optional source-review mode keeps the two-agent manager workflow. The Lead
still has only `review_ui`. In this mode, the UI Agent additionally receives
`read_sample_files(paths)`, a single batch-read tool for a bundled fictional
Lantern Board frontend. There are no new dependencies, external repositories,
shell commands, file writes, browser tools, or network tools available to agents.

## Run it

Use Python 3.10+, install `requirements.txt`, and configure `OPENAI_API_KEY` in the
environment or this checkout's private `.env` as described in the README. Source
mode requires POSIX `dir_fd`, `O_NOFOLLOW`, and `O_DIRECTORY` support (tested on
Linux). It fails closed on unsupported systems; description-only mode remains
available. Python 3.10 syntax is checked; runtime testing uses Python 3.12.

```bash
python -m unittest discover -s tests -v
python h2o.py --sample-codebase --task-file examples/codebase_task.txt
```

The CLI prints a cited final recommendation to standard output and source
filenames, line counts, and SHA-256 hashes to standard error. Without
`--sample-codebase`, neither agent receives a file tool. There is no option to
select another source directory or repository.

The fixture contains intentional navigation, contrast, focus, form-label, and
confirmation-content problems. It is plain HTML/CSS/JavaScript and has no backend,
real reservations, or personal data. The agents read its text; they do not run it.

## Enforced access boundary

| Control | Implementation |
| --- | --- |
| Fixed root | Only this checkout's `examples/sample_frontend/`; no model-supplied root. |
| Exact allowlist | `index.html`, `styles.css`, `app.js`, with exact spelling. No directory listing or globbing. |
| One batch | At most one attempt per workflow, including denied attempts; one to three unique filenames. |
| File limit | At most 16 KiB per file and 32 KiB total per batch. |
| File types | Nonempty UTF-8 regular files with no NUL bytes and exactly one hard link. |
| Safe opens | Read-only, directory-relative descriptors with `O_NOFOLLOW` at each directory and file component. |
| Special files | Directories, FIFOs, symlinks, hard links, and unreadable files are rejected. |
| Partial failures | No partial batch or source metadata is published when any requested file fails. |
| Concurrent changes | Stable directory descriptors prevent path-redirection races; changed file size/timestamps/link count during a read cause rejection. |

Absolute paths, `..`, Windows path forms, encoded traversal, hidden files,
`.env`, `.git`, and unknown names fail exact allowlist validation before filesystem
opens. Permission enforcement lives in Python, not in model instructions. The
tool does not load environment variables or credential-file paths. No
operating-system or repository permissions are changed.

Each successful batch returns only relative filenames, hashes, line counts, and
numbered source text, marked as untrusted fictional data. Source and comments
are treated as data rather than instructions. Trusted project code and fixture
selection remain under the operator's control; this tool is not a general
filesystem sandbox or a secret-content scanner.

## Delegation and citations

Source mode requires one UI review. The UI Agent's first turn requires its read
tool; its second turn produces the review. The Lead then synthesizes. The
existing Lead/UI limits remain four/two model turns. A second file read or a
second source review in the same workflow fails the read budget.

Both the UI review and Lead recommendation must include bracketed citations such
as `[index.html:12]` or `[styles.css:1-6]`. The application checks that at least one
source citation exists and that every bracketed group containing a colon
references files actually read with valid line ranges. Grouped references such
as `[index.html:12, app.js:2-5]` and `[index.html:13, 29]` are supported. Missing,
malformed, unknown-file, or out-of-range
citations fail the workflow. `Review.source_files` exposes immutable source
metadata to callers.

These checks establish provenance and valid references, not the semantic truth
of a finding or the completeness of its citations. Inspect findings against the
numbered source. Agents must distinguish source observations from assumptions
and cannot claim browser, visual, accessibility-audit, or user-test results.

## Optional live integration test

```bash
python live_test.py --sample-codebase             # skips, no API calls
mkdir -p artifacts
python live_test.py --run-live --sample-codebase > artifacts/source-live-validation.json
python -m json.tool artifacts/source-live-validation.json
```

Only `--run-live` permits billable API requests. A normal source run uses four
model requests: Lead delegation, UI read-tool selection, UI review, Lead
synthesis. Both agents' input/output tokens are billed; returned source text
becomes model input. The same 1,200-output-token/request cap, disabled retries,
and 180-second orchestration timeout apply. See the existing
[live-validation billing guidance](LIVE_VALIDATION.md#billing-and-limits).

The JSON report adds `source_access`, `source_files`, `source_read_limits`, and
the actual source-tool output delivered to the UI Agent. Nested tool results are
correlated by call ID rather than completion order. Source-access denials have
their own failure category; citation/response failures remain `response`.
Reports remain in gitignored `artifacts/`; never commit `.env` or API keys.

## Offline coverage

The original 21 tests remain unchanged. New tests exercise actual safe file opens,
allowlist/traversal rejection, links and directory swaps, special files, invalid
text, both byte limits, the one-attempt budget, all-or-nothing results, and citation
validation. A scripted model drives the real SDK through the nested file-tool
workflow, checking source delivery to the UI, review delivery to the Lead, valid
citations, the CLI/live-test opt-in flags, and unchanged fixture bytes afterward.
Offline test discovery makes no API calls.

## Validation record: November 16, 2026

All **42 offline tests passed**, including the original 21 unchanged tests and 21
new source-access tests. Dependency consistency, Python 3.10 syntax, and diff
checks passed. Runtime: Python 3.12.14, official Agents SDK 0.24.0, Linux.

One live source-mode run with `gpt-4.1-mini` completed with exit code 0. All five
checks passed: configuration, authentication, delegation, response, and
source access. It made four model requests and two nested tool calls:

1. Lead → `review_ui`.
2. UI → `read_sample_files` for the three allowed files.
3. UI reviewed the returned numbered source.
4. Lead synthesized the cited UI findings.

Reported usage was **3,630 input tokens and 1,565 output tokens**, with no automatic
retries. The source files were 32 HTML lines, 7 CSS lines, and 10 JavaScript lines.
Their SHA-256 hashes matched the files on disk after the run. The complete report
is retained locally at `artifacts/source-live-validation-20261116.json`, with no
key or `.env` copied into this checkout or committed.

The model used grouped citations; the checker was extended to validate those
groups rather than skipping them. Both saved live outputs passed the stricter
checker, and the saved call-ID/data-flow evidence was revalidated without another
billable run.

Manual review found recommendation-quality limits: the model suggested explicit
Enter/Space handlers for a native button that already has keyboard activation,
and made readability/mobile-usability assessments without rendering the app.
Do not apply those suggestions automatically. The pass establishes bounded source
access, data flow, and valid citation locations; the recommendations still need
human technical review. No proposed frontend fixes were implemented.
