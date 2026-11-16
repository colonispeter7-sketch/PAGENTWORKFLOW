"""Agent registry and bounded review service. No MCP transport dependencies."""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Awaitable, Callable, Literal

from agents import ModelRetrySettings, ModelSettings
from agents.models.openai_responses import OpenAIResponsesModel
from openai import AsyncOpenAI, AuthenticationError, APIConnectionError, APIStatusError, RateLimitError
from pydantic import BaseModel, ConfigDict, Field, field_validator

import h2o
from sample_codebase import SampleAccessError, SourceFile

MAX_OBJECTIVE_CHARS = 8_000
REVIEW_TIMEOUT = 180
REVIEWS_PER_MINUTE = 3


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    objective: str = Field(min_length=1, max_length=MAX_OBJECTIVE_CHARS)
    mode: Literal["description", "sample_codebase"] = "description"

    @field_validator("objective")
    @classmethod
    def validate_objective(cls, text: str) -> str:
        if not text.strip():
            raise ValueError("An objective is required.")
        if re.search(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{16,}\b", text):
            raise ValueError("Do not submit credentials.")
        return text


class ToolResponse(BaseModel):
    ok: bool
    code: str
    message: str
    data: dict = Field(default_factory=dict)


def failure(code: str, message: str) -> ToolResponse:
    return ToolResponse(ok=False, code=code, message=message)


@dataclass(frozen=True)
class ReviewResult:
    recommendation: str
    specialist_reviews: tuple[str, ...]
    source_files: tuple[SourceFile, ...] = ()


ReviewRunner = Callable[[ReviewRequest], Awaitable[ReviewResult]]


@dataclass(frozen=True)
class AgentCapability:
    id: str
    name: str
    role: Literal["manager", "specialist"]
    description: str
    read_only: bool = True


class AgentRegistry:
    """Trusted application registration, never dynamic imports from tool input.

    A new specialist must be implemented and wired into the manager's SDK
    workflow before registration. Metadata alone does not implement an agent.
    """

    def __init__(self, manager_id: str):
        self.manager_id = manager_id
        self._agents: dict[str, AgentCapability] = {}
        self._runners: dict[str, ReviewRunner] = {}

    def register(self, capability: AgentCapability, runner: ReviewRunner | None = None) -> None:
        if capability.read_only is not True:
            raise ValueError("Code-changing agents require a separate human-approval workflow.")
        if capability.role not in ("manager", "specialist"):
            raise ValueError("Unsupported agent role.")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", capability.id) or capability.id in self._agents:
            raise ValueError("Invalid or duplicate agent identifier.")
        if capability.role == "manager" and runner is None:
            raise ValueError("A manager requires an implemented workflow runner.")
        self._agents[capability.id] = capability
        if runner is not None:
            self._runners[capability.id] = runner

    def capabilities(self) -> list[dict]:
        return [asdict(agent) for agent in self._agents.values()]

    async def review(self, request: ReviewRequest) -> ReviewResult:
        manager = self._agents.get(self.manager_id)
        if manager is None or manager.role != "manager":
            raise RuntimeError("No manager workflow is configured.")
        return await self._runners[self.manager_id](request)


class OpenAILeadRunner:
    """Reuse the existing agents/reader; keep credentials out of agent context."""

    def __init__(self, model: str = h2o.DEFAULT_MODEL):
        self.model = model

    async def __call__(self, request: ReviewRequest) -> ReviewResult:
        async with AsyncOpenAI(
            api_key=os.environ["OPENAI_API_KEY"], base_url="https://api.openai.com/v1",
            max_retries=0, timeout=45,
        ) as client:
            result = await h2o.review(
                request.objective,
                OpenAIResponsesModel(model=self.model, openai_client=client),
                sample_codebase=request.mode == "sample_codebase",
                model_settings=ModelSettings(
                    max_tokens=1200, store=False, retry=ModelRetrySettings(max_retries=0),
                ),
            )
            return ReviewResult(result.recommendation, result.ui_reviews, result.source_files)


def default_registry(model: str = h2o.DEFAULT_MODEL) -> AgentRegistry:
    registry = AgentRegistry(manager_id="lead")
    registry.register(AgentCapability(
        "lead", "Lead Agent", "manager", "Delegates specialist reviews and synthesizes recommendations."
    ), OpenAILeadRunner(model))
    registry.register(AgentCapability(
        "ui", "UI Agent", "specialist", "Reviews navigation, usability, accessibility, and design through the Lead."
    ))
    return registry


def review_failure(error: Exception) -> ToolResponse:
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        for types, code, message in [
            (AuthenticationError, "backend_authentication", "The operator must check the backend API credential."),
            (RateLimitError, "backend_limit", "Backend API quota or rate limit reached; inspect billing before retrying."),
            ((TimeoutError, asyncio.TimeoutError, APIConnectionError), "backend_unavailable", "The review timed out or could not reach the backend."),
            (SampleAccessError, "source_access", "The bounded fictional source read was denied."),
            (h2o.ResponseError, "response", "The agents did not produce a valid response or source citations."),
            (h2o.DelegationError, "delegation", "The specialist review did not complete."),
            (APIStatusError, "backend_configuration", "The operator must check backend model access and configuration."),
        ]:
            if isinstance(error, types):
                return failure(code, message)
        error = error.__cause__ or error.__context__
    return failure("review_failed", "The review failed; no validated recommendation was produced.")


class ReviewService:
    def __init__(self, registry: AgentRegistry, *, enable_reviews: bool = False):
        self.registry = registry
        self.enable_reviews = enable_reviews
        self._active = asyncio.Lock()
        self._started: deque[float] = deque()

    def capabilities(self) -> ToolResponse:
        return ToolResponse(ok=True, code="capabilities", message="Available read-only review capabilities.", data={
            "agents": self.registry.capabilities(), "manager_id": self.registry.manager_id,
            "review_modes": ["description", "sample_codebase"],
            "reviews_enabled": self.enable_reviews,
            "review_requests_are_billable": True,
            "human_approval_required_for_future_code_changes": True,
            "code_changes_supported": False,
            "limits": {"objective_chars": MAX_OBJECTIVE_CHARS, "concurrent_reviews": 1,
                       "reviews_per_minute_per_process": REVIEWS_PER_MINUTE, "timeout_seconds": REVIEW_TIMEOUT},
        })

    async def submit(self, request: ReviewRequest) -> ToolResponse:
        if not self.enable_reviews:
            return failure("reviews_disabled", "The operator must explicitly enable billable reviews.")
        key = os.getenv("OPENAI_API_KEY", "")
        if not key.strip():
            return failure("backend_configuration", "The operator must configure the backend API credential privately.")
        if key in request.objective or re.search(r"\bHHC\b", request.objective, re.IGNORECASE):
            return failure("restricted_input", "Use fictional review data only; do not submit secrets or HHC data.")
        if self._active.locked():
            return failure("busy", "A review is already running; try again after it completes.")
        async with self._active:
            now = time.monotonic()
            while self._started and now - self._started[0] >= 60:
                self._started.popleft()
            if len(self._started) >= REVIEWS_PER_MINUTE:
                return failure("rate_limited", "The process review limit is reached; wait before retrying.")
            self._started.append(now)
            try:
                result = await asyncio.wait_for(self.registry.review(request), timeout=REVIEW_TIMEOUT)
                if not result.recommendation.strip() or not result.specialist_reviews:
                    return failure("response", "The workflow returned an incomplete review.")
                # Never return a configured backend credential, even if a faulty
                # custom runner echoed it. OAuth credentials never enter this layer.
                def safe(value):
                    if isinstance(value, str):
                        return re.sub(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{16,}\b", "[REDACTED]", value.replace(key, "[REDACTED]"))
                    if isinstance(value, list):
                        return [safe(item) for item in value]
                    if isinstance(value, dict):
                        return {safe(name): safe(item) for name, item in value.items()}
                    return value
                return ToolResponse(ok=True, code="review_complete", message="Lead review completed; human review of recommendations is required.", data={
                    "recommendation": safe(result.recommendation),
                    "specialist_reviews": [safe(text) for text in result.specialist_reviews],
                    "source_files": safe([asdict(source) for source in result.source_files]),
                    "manual_quality_review_required": True,
                })
            except Exception as error:
                return review_failure(error)
