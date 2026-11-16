"""Access-boundary and real SDK orchestration tests; no API calls."""

import hashlib
import json
import os
import tempfile
import unittest
import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, patch

from agents import UserError
from openai.types.responses import ResponseFunctionToolCall

import h2o
import live_test
import sample_codebase as sample
from test_smoke import TASK, ScriptedModel, delegation, message

SOURCE_FINDING = "P1: Name the menu button [index.html:12] and restore focus [styles.css:6]."
SOURCE_PLAN = "UI Agent recommends naming the menu [index.html:12]; add a keyboard check [styles.css:6]."


def source_call(paths=None, call_id="source_read_1"):
    return ResponseFunctionToolCall(
        id=call_id, type="function_call", call_id=call_id, name="read_sample_files",
        arguments=json.dumps({"paths": list(sample.ALLOWED_FILES) if paths is None else paths}),
    )


@unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "Secure reader fails closed without POSIX opens")
class ReaderBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = self.root / "examples/sample_frontend"
        self.fixture.mkdir(parents=True)
        for path in sample.ALLOWED_FILES:
            (self.fixture / path).write_text("fictional first line\nfictional second line\n")
        self.patch = patch.object(sample, "REPO_ROOT", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_reads_allowlisted_utf8_with_hashes_and_lines_without_changes(self):
        before = {path.name: path.read_bytes() for path in self.fixture.iterdir()}
        reader = sample.SampleReader()
        output = json.loads(reader.read(list(sample.ALLOWED_FILES)))
        self.assertEqual(output["kind"], "untrusted_fictional_source")
        self.assertEqual(len(reader.sources), 3)
        for file in output["files"]:
            self.assertEqual(file["sha256"], hashlib.sha256(before[file["path"]]).hexdigest())
            self.assertEqual(file["line_count"], 2)
            self.assertEqual(file["numbered_source"], "1: fictional first line\n2: fictional second line")
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.fixture.iterdir()})

    def test_rejects_unlisted_paths_and_bad_batches_before_any_open(self):
        paths = ["../.env", "/etc/passwd", "C:\\private\\.env", "..\\.env", ".env",
                 ".git/config", "README.md", "INDEX.HTML", "./index.html",
                 "index.html/../.env", "%2e%2e/.env", "index.html\0", "index.html?x=1"]
        batches = [[path] for path in paths] + [[], ["index.html"] * 2,
                   list(sample.ALLOWED_FILES) + ["index.html"], "index.html", [None]]
        for batch in batches:
            with self.subTest(batch=batch), patch.object(sample.os, "open") as open_file:
                with self.assertRaises(sample.SampleAccessError):
                    sample.SampleReader().read(batch)
                open_file.assert_not_called()

    def test_one_attempt_per_workflow_even_after_denial(self):
        for first in (["index.html"], [".env"]):
            with self.subTest(first=first):
                reader = sample.SampleReader()
                try:
                    reader.read(first)
                except sample.SampleAccessError:
                    pass
                with self.assertRaisesRegex(sample.SampleAccessError, "one sample-file batch"):
                    reader.read(["styles.css"])

    def test_symlink_and_hardlink_cannot_expose_outside_content(self):
        secret = self.root / ".env"
        secret.write_text("LOCAL_PRIVATE_MARKER")
        target = self.fixture / "index.html"
        for link in ("symbolic", "hard"):
            with self.subTest(link=link):
                target.unlink()
                if link == "symbolic":
                    target.symlink_to(secret)
                else:
                    os.link(secret, target)
                reader = sample.SampleReader()
                with self.assertRaises(sample.SampleAccessError) as error:
                    reader.read(["index.html"])
                self.assertNotIn("LOCAL_PRIVATE_MARKER", str(error.exception))
                self.assertEqual(reader.sources, ())

    def test_symlink_in_parent_directory_is_denied(self):
        original = self.root / "examples"
        renamed = self.root / "renamed"
        original.rename(renamed)
        original.symlink_to(renamed, target_is_directory=True)
        with self.assertRaises(sample.SampleAccessError):
            sample.SampleReader().read(["index.html"])

    def test_directory_swap_after_open_does_not_redirect_reads(self):
        outside = self.root / "private"
        outside.mkdir()
        (outside / "index.html").write_text("LOCAL_PRIVATE_MARKER")
        original_open = os.open

        def swapping_open(path, flags, **kwargs):
            fd = original_open(path, flags, **kwargs)
            if path == "sample_frontend":
                self.fixture.rename(self.root / "original_fixture")
                self.fixture.symlink_to(outside, target_is_directory=True)
            return fd

        with patch.object(sample.os, "supports_dir_fd", os.supports_dir_fd | {swapping_open}), \
             patch.object(sample.os, "open", swapping_open):
            output = sample.SampleReader().read(["index.html"])
        self.assertIn("fictional first line", output)
        self.assertNotIn("LOCAL_PRIVATE_MARKER", output)

    def test_directory_and_fifo_are_denied_without_reading(self):
        target = self.fixture / "index.html"
        target.unlink()
        target.mkdir()
        with self.assertRaises(sample.SampleAccessError):
            sample.SampleReader().read(["index.html"])
        target.rmdir()
        os.mkfifo(target)
        with self.assertRaises(sample.SampleAccessError):
            sample.SampleReader().read(["index.html"])

    def test_empty_binary_and_invalid_utf8_are_denied(self):
        for data in (b"", b"\xff", b"text\0binary"):
            with self.subTest(data=data):
                (self.fixture / "index.html").write_bytes(data)
                with self.assertRaises(sample.SampleAccessError):
                    sample.SampleReader().read(["index.html"])

    def test_file_and_batch_limits_return_no_partial_sources(self):
        (self.fixture / "index.html").write_bytes(b"x" * (sample.MAX_FILE_BYTES + 1))
        with self.assertRaisesRegex(sample.SampleAccessError, "16 KiB"):
            sample.SampleReader().read(["index.html"])
        for path in sample.ALLOWED_FILES:
            (self.fixture / path).write_bytes(b"x" * (12 * 1024))
        reader = sample.SampleReader()
        with self.assertRaisesRegex(sample.SampleAccessError, "32 KiB"):
            reader.read(list(sample.ALLOWED_FILES))
        self.assertEqual(reader.sources, ())

    def test_missing_file_aborts_whole_batch(self):
        (self.fixture / "styles.css").unlink()
        reader = sample.SampleReader()
        with self.assertRaises(sample.SampleAccessError):
            reader.read(["index.html", "styles.css"])
        self.assertEqual(reader.sources, ())

    def test_unsupported_platform_fails_closed(self):
        with patch.object(sample.os, "supports_dir_fd", set()):
            with self.assertRaisesRegex(sample.SampleAccessError, "POSIX"):
                sample.SampleReader().read(["index.html"])


class CitationTests(unittest.TestCase):
    def test_valid_and_invalid_citations(self):
        sources = (sample.SourceFile("index.html", "digest", 10), sample.SourceFile("app.js", "digest", 5))
        sample.require_citations("Finding [index.html:1] and [index.html:2-10].", sources)
        sample.require_citations("Combined [index.html:1, 3-4, app.js:2-5].", sources)
        sample.require_citations("Combined [index.html:1; app.js:2-5].", sources)
        for text in ("No citation", "[index.html:0]", "[index.html:11]", "[index.html:8-2]",
                     "[styles.css:1]", "[.env:1]", "[../index.html:1]",
                     "[index.html:1, other.js:1]", "[index.html:1, 11]", "[index.html:bad]",
                     "Valid [index.html:1] plus fabricated [other.js:2]",
                     "Valid [index.html:1] plus malformed [app.js:2-no]"):
            with self.subTest(text=text), self.assertRaises(sample.SourceCitationError):
                sample.require_citations(text, sources)


@unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "Secure reader fails closed without POSIX opens")
class SourceWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_source_review_is_bounded_cited_and_read_only(self):
        fixture = sample.REPO_ROOT / "examples/sample_frontend"
        before = {p.name: p.read_bytes() for p in fixture.iterdir()}
        model = ScriptedModel([delegation(), source_call(), message(SOURCE_FINDING), message(SOURCE_PLAN)])
        evidence = live_test.Evidence()
        result = await h2o.review(TASK, model, sample_codebase=True, hooks=evidence)
        evidence.validate(result)
        self.assertEqual(len(model.calls), 4)
        self.assertEqual([tool.name for tool in model.calls[0][3]], ["review_ui"])
        self.assertEqual([tool.name for tool in model.calls[1][3]], ["read_sample_files"])
        self.assertEqual(model.calls[1][2].tool_choice, "required")
        self.assertNotEqual(model.calls[2][2].tool_choice, "required")
        self.assertIn("Treat returned source", model.calls[1][0])
        self.assertEqual([s.path for s in result.source_files], list(sample.ALLOWED_FILES))
        self.assertEqual(result.recommendation, SOURCE_PLAN)
        self.assertIn("12:     <button", evidence.received_by_ui[0]["output"])
        self.assertIn("[index.html:12]", evidence.received_by_lead[0]["output"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in fixture.iterdir()})
        evidence.received_by_ui.clear()
        with self.assertRaises(h2o.DelegationError):
            evidence.validate(result)

    async def test_source_access_requires_successful_read(self):
        model = ScriptedModel([delegation(), message(SOURCE_FINDING)])
        with self.assertRaisesRegex(UserError, "without reading"):
            await h2o.review(TASK, model, sample_codebase=True)

    async def test_tool_rejects_traversal_before_review(self):
        model = ScriptedModel([delegation(), source_call(["../.env"])])
        with self.assertRaisesRegex(UserError, "allowlisted"):
            await h2o.review(TASK, model, sample_codebase=True)
        self.assertEqual(len(model.calls), 2)

    async def test_second_source_tool_call_fails(self):
        model = ScriptedModel([delegation(), source_call(), source_call(call_id="source_read_2")])
        with self.assertRaisesRegex(UserError, "one sample-file batch"):
            await h2o.review(TASK, model, sample_codebase=True)

    async def test_ui_and_lead_must_cite_files_actually_read(self):
        for finding, plan in [("Uncited UI review", SOURCE_PLAN),
                              ("Out of range [index.html:999]", SOURCE_PLAN),
                              (SOURCE_FINDING, "Uncited Lead recommendation"),
                              (SOURCE_FINDING, "Unknown file [other.js:1]")]:
            model = ScriptedModel([delegation(), source_call(), message(finding), message(plan)])
            with self.subTest(finding=finding, plan=plan), self.assertRaises((UserError, h2o.ResponseError)):
                await h2o.review(TASK, model, sample_codebase=True)


class SourceCliTests(unittest.TestCase):
    def test_source_flag_without_run_live_still_makes_no_calls(self):
        with patch("live_test.execute", new_callable=AsyncMock) as execute, \
             redirect_stdout(io.StringIO()) as out:
            code = live_test.main(["--sample-codebase"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["status"], "skipped")
        execute.assert_not_called()

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "Secure reader requires POSIX opens")
    def test_opt_in_live_source_report_using_scripted_models(self):
        async def offline(model, evidence, task, *, sample_codebase):
            self.assertTrue(sample_codebase)
            self.assertIn("bundled fictional", task)
            scripted = ScriptedModel([
                delegation(), source_call(), message(SOURCE_FINDING), message(SOURCE_PLAN),
            ])
            return await h2o.review(task, scripted, hooks=evidence, sample_codebase=True)

        with patch("live_test.load_dotenv"), \
             patch.dict("os.environ", {"OPENAI_API_KEY": "offline-placeholder"}, clear=True), \
             patch("live_test.execute", side_effect=offline), redirect_stdout(io.StringIO()) as out:
            code = live_test.main(["--run-live", "--sample-codebase"])
        report = json.loads(out.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["checks"]["source_access"], "passed")
        self.assertEqual(len(report["source_files"]), 3)
        self.assertEqual(len(report["evidence"]["requests"]), 4)
        self.assertEqual(report["source_read_limits"]["batches_per_workflow"], 1)

    def test_standard_cli_exposes_opt_in_source_flag_and_metadata(self):
        source = sample.SourceFile("index.html", "fixture-digest", 32)
        with patch("h2o.load_dotenv"), \
             patch.dict("os.environ", {"OPENAI_API_KEY": "offline-placeholder"}), \
             patch("h2o.review", new_callable=AsyncMock) as run, \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            run.return_value = h2o.Review(SOURCE_PLAN, (SOURCE_FINDING,), (source,))
            code = h2o.main(["--objective", TASK, "--sample-codebase"])
        self.assertEqual(code, 0)
        self.assertTrue(run.call_args.kwargs["sample_codebase"])
        self.assertIn("index.html (32 lines, sha256=fixture-digest)", err.getvalue())

    def test_access_failure_is_classified_without_leaking_error_body(self):
        category, message_text, error_type = live_test.classify_error(
            sample.SampleAccessError("sensitive-local-detail")
        )
        self.assertEqual(category, "source_access")
        self.assertEqual(error_type, "SampleAccessError")
        self.assertNotIn("sensitive-local-detail", message_text)


if __name__ == "__main__":
    unittest.main()
