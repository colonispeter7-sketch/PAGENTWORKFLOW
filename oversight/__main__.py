"""Read-only oversight CLI; offline by default, live execution requires opt-in."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

from agents import ModelBehaviorError, MaxTurnsExceeded
from agents.models.openai_responses import OpenAIResponsesModel
from openai import AsyncOpenAI, AuthenticationError, APIConnectionError, APIStatusError, RateLimitError

import h2o
from oversight.evidence import MAX_DIFF_BYTES, ROOT, OversightError
from oversight.review import review_diff


def error_details(error: Exception) -> tuple[str, str]:
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, OversightError):
            return error.code, str(error)
        for types, code, message in [
            (AuthenticationError, "authentication", "Check the OpenAI API credential locally."),
            (RateLimitError, "api_limit", "Check API quota and billing before retrying."),
            ((TimeoutError, APIConnectionError), "unavailable", "The review timed out or the API was unreachable."),
            (APIStatusError, "configuration", "Check model access and API configuration."),
            (h2o.DelegationError, "delegation", "The UI delegation did not complete."),
            ((h2o.ResponseError, ModelBehaviorError, MaxTurnsExceeded), "response", "The agents did not complete a valid bounded response."),
        ]:
            if isinstance(error, types):
                return code, message
        error = error.__cause__ or error.__context__
    return "review_failed", "No validated oversight report was produced."


async def live_review(text: str, model: str):
    # No .env loading or credential access in offline mode. Never put the key in
    # an agent prompt. OPENAI_BASE_URL cannot override the fixed API endpoint.
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise OversightError("configuration", "Set OPENAI_API_KEY in the process environment before an explicitly enabled live run.")
    async with AsyncOpenAI(api_key=key, base_url="https://api.openai.com/v1",
                          max_retries=0, timeout=45) as client:
        return await review_diff(text, OpenAIResponsesModel(model=model, openai_client=client))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--demo", action="store_true", help="Review the bundled fictional diff")
    source.add_argument("--diff-stdin", action="store_true", help="Read bounded fictional unified diff text from stdin")
    parser.add_argument("--run-live", action="store_true", help="Explicitly enable billable model calls (default: synthetic offline demo)")
    parser.add_argument("--model", default=h2o.DEFAULT_MODEL)
    args = parser.parse_args(argv)
    # Provider/SDK diagnostics can contain raw response bodies; emit only the
    # structured, classified errors below from this command.
    for name in ("openai", "openai.agents", "httpx", "httpx2"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    try:
        if args.demo:
            with (ROOT / "examples" / "oversight_change.diff").open("rb") as stream:
                data = stream.read(MAX_DIFF_BYTES + 1)
        else:
            data = sys.stdin.buffer.read(MAX_DIFF_BYTES + 1)
        if len(data) > MAX_DIFF_BYTES:
            raise OversightError("diff_input", "The supplied diff exceeds 32 KiB.")
        try:
            text = data.decode("utf-8")
        except UnicodeError:
            raise OversightError("diff_input", "Supply a UTF-8 fictional diff.") from None
        result = asyncio.run(live_review(text, args.model) if args.run_live else review_diff(text, offline_demo=True))
    except Exception as exc:
        code, message = error_details(exc)
        print(json.dumps({"ok": False, "code": code, "message": message}), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "report": result.model_dump()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
