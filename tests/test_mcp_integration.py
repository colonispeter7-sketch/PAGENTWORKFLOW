"""Offline tests of real MCP protocol, review adapters, and protected HTTP."""

import asyncio
import json
import os
import sys
import time
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import anyio
import httpx2 as httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.memory import create_client_server_memory_streams

import h2o
from h2o_integration.auth import OAuthConfig, OAuthJWTVerifier, SCOPE
from h2o_integration.server import create_http_app, create_server
from h2o_integration.service import (
    AgentCapability, AgentRegistry, OpenAILeadRunner, ReviewRequest, ReviewResult,
    ReviewService, default_registry,
)
from test_smoke import ScriptedModel, delegation, message
from test_sample_codebase import source_call, SOURCE_FINDING, SOURCE_PLAN

TASK = "Review the fictional Lantern Board mobile navigation."
AUTH = OAuthConfig("https://h2o.example/mcp", "https://identity.example/", "https://identity.example/keys")


def registry_for(runner):
    registry = AgentRegistry("lead")
    registry.register(AgentCapability("lead", "Lead", "manager", "Read-only manager"), runner)
    registry.register(AgentCapability("ui", "UI", "specialist", "Read-only frontend reviewer"))
    return registry


@asynccontextmanager
async def connected(server):
    async with create_client_server_memory_streams() as (client, remote):
        async with anyio.create_task_group() as group:
            lowlevel = server._lowlevel_server
            group.start_soon(lowlevel.run, *remote, lowlevel.create_initialization_options())
            async with ClientSession(*client) as session:
                await session.initialize()
                yield session
            group.cancel_scope.cancel()


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_registration_and_capabilities_are_structured_and_read_only(self):
        registry = registry_for(AsyncMock())
        registry.register(AgentCapability("qa", "QA reviewer", "specialist", "Test implementation fixture"))
        async with connected(create_server(ReviewService(registry))) as session:
            tools = (await session.list_tools()).tools
            self.assertEqual({tool.name for tool in tools}, {"get_agent_capabilities", "submit_review"})
            for tool in tools:
                self.assertTrue(tool.annotations.read_only_hint)
                self.assertFalse(tool.annotations.destructive_hint)
                self.assertFalse(tool.annotations.open_world_hint)
                self.assertTrue(tool.output_schema)
                self.assertFalse(tool.input_schema["additionalProperties"])
            response = await session.call_tool("get_agent_capabilities", {})
        self.assertFalse(response.is_error)
        data = response.structured_content["data"]
        self.assertEqual([agent["id"] for agent in data["agents"]], ["lead", "ui", "qa"])
        self.assertFalse(data["code_changes_supported"])
        self.assertTrue(data["human_approval_required_for_future_code_changes"])

    async def test_disabled_reviews_return_mcp_error_without_api_calls(self):
        runner = AsyncMock()
        async with connected(create_server(ReviewService(registry_for(runner)))) as session:
            response = await session.call_tool("submit_review", {"request": {"objective": TASK}})
        self.assertTrue(response.is_error)
        self.assertEqual(response.structured_content["code"], "reviews_disabled")
        runner.assert_not_called()

    async def test_invalid_requests_and_unavailable_write_tools_do_not_echo_inputs(self):
        runner = AsyncMock()
        server = create_server(ReviewService(registry_for(runner), enable_reviews=True))
        bad = [
            {"request": {"objective": ""}},
            {"request": {"objective": "x" * 8001}},
            {"request": {"objective": TASK, "root": "/private", "approval": True}},
            {"request": {"objective": TASK, "mode": "write_code"}},
            {"request": {"objective": TASK}, "api_key": "private-test-marker"},
            {"request": {"objective": "sk-" + "x" * 32}},
        ]
        async with connected(server) as session:
            for arguments in bad:
                with self.subTest(arguments=list(arguments)):
                    response = await session.call_tool("submit_review", arguments)
                    self.assertTrue(response.is_error)
                    self.assertEqual(response.structured_content["code"], "invalid_request")
                    self.assertNotIn("private-test-marker", str(response))
                    self.assertNotIn("/private", str(response))
            response = await session.call_tool("write_file", {"path": "private-test-marker", "approved": True})
            self.assertTrue(response.is_error)
            self.assertEqual(response.structured_content["code"], "unsupported_tool")
            self.assertNotIn("private-test-marker", str(response))
        runner.assert_not_called()

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "The existing source reader requires POSIX opens")
    async def test_real_sdk_source_review_through_mcp_preserves_existing_reader(self):
        scripted = ScriptedModel([delegation(), source_call(), message(SOURCE_FINDING), message(SOURCE_PLAN)])

        async def runner(request):
            review = await h2o.review(request.objective, scripted, sample_codebase=True)
            return ReviewResult(review.recommendation, review.ui_reviews, review.source_files)

        service = ReviewService(registry_for(runner), enable_reviews=True)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "offline-placeholder"}):
            async with connected(create_server(service)) as session:
                response = await session.call_tool("submit_review", {
                    "request": {"objective": TASK, "mode": "sample_codebase"},
                })
        self.assertFalse(response.is_error)
        data = response.structured_content["data"]
        self.assertEqual(data["recommendation"], SOURCE_PLAN)
        self.assertEqual(len(data["source_files"]), 3)
        self.assertEqual(len(scripted.calls), 4)
        self.assertTrue(data["manual_quality_review_required"])

    async def test_real_stdio_subprocess_can_initialize_and_discover(self):
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "h2o_integration"], cwd=os.getcwd(),
            env={"H2O_MCP_ENABLE_REVIEWS": "0"},
        )
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                response = await session.call_tool("get_agent_capabilities", {})
        self.assertTrue(response.structured_content["ok"])
        self.assertFalse(response.structured_content["data"]["reviews_enabled"])


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        key = patch.dict(os.environ, {"OPENAI_API_KEY": "offline-placeholder"})
        key.start()
        self.addCleanup(key.stop)

    async def test_missing_backend_key_is_configuration_error(self):
        service = ReviewService(registry_for(AsyncMock()), enable_reviews=True)
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            response = await service.submit(ReviewRequest(objective=TASK))
        self.assertEqual(response.code, "backend_configuration")

    async def test_restricted_input_never_reaches_runner(self):
        runner = AsyncMock()
        service = ReviewService(registry_for(runner), enable_reviews=True)
        for objective in ("Inspect HHC data", "Review offline-placeholder"):
            response = await service.submit(ReviewRequest(objective=objective))
            self.assertEqual(response.code, "restricted_input")
        runner.assert_not_called()

    async def test_failure_redaction_and_timeout(self):
        for error in (RuntimeError("private-test-marker"), h2o.DelegationError("private-test-marker")):
            service = ReviewService(registry_for(AsyncMock(side_effect=error)), enable_reviews=True)
            response = await service.submit(ReviewRequest(objective=TASK))
            self.assertFalse(response.ok)
            self.assertNotIn("private-test-marker", response.model_dump_json())

        async def slow(request):
            await asyncio.Event().wait()
        service = ReviewService(registry_for(slow), enable_reviews=True)
        with patch("h2o_integration.service.REVIEW_TIMEOUT", 0.01):
            response = await service.submit(ReviewRequest(objective=TASK))
        self.assertEqual(response.code, "backend_unavailable")

    async def test_concurrency_and_rate_limits_bound_billable_calls(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def slow(request):
            started.set()
            await release.wait()
            return ReviewResult("Recommendation", ("Finding",))
        service = ReviewService(registry_for(slow), enable_reviews=True)
        task = asyncio.create_task(service.submit(ReviewRequest(objective=TASK)))
        await started.wait()
        self.assertEqual((await service.submit(ReviewRequest(objective=TASK))).code, "busy")
        release.set()
        self.assertTrue((await task).ok)
        for _ in range(2):
            self.assertTrue((await service.submit(ReviewRequest(objective=TASK))).ok)
        self.assertEqual((await service.submit(ReviewRequest(objective=TASK))).code, "rate_limited")

    async def test_result_redacts_configured_key(self):
        service = ReviewService(registry_for(AsyncMock(return_value=ReviewResult(
            "Contains offline-placeholder", ("Also offline-placeholder",)
        ))), enable_reviews=True)
        response = await service.submit(ReviewRequest(objective=TASK))
        self.assertNotIn("offline-placeholder", response.model_dump_json())
        self.assertIn("[REDACTED]", response.model_dump_json())

    async def test_adapter_preserves_backend_settings_and_source_mode(self):
        client = Mock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False))
        with patch("h2o_integration.service.AsyncOpenAI", return_value=client) as constructor, \
             patch("h2o_integration.service.OpenAIResponsesModel", return_value="test-model"), \
             patch("h2o.review", new_callable=AsyncMock, return_value=h2o.Review("Plan", ("Finding",))) as run:
            await OpenAILeadRunner()(ReviewRequest(objective=TASK, mode="sample_codebase"))
        self.assertEqual(constructor.call_args.kwargs["base_url"], "https://api.openai.com/v1")
        self.assertEqual(constructor.call_args.kwargs["max_retries"], 0)
        self.assertTrue(run.call_args.kwargs["sample_codebase"])
        settings = run.call_args.kwargs["model_settings"]
        self.assertFalse(settings.store)
        self.assertEqual(settings.retry.max_retries, 0)
        self.assertEqual(settings.max_tokens, 1200)


class RegistryTests(unittest.TestCase):
    def test_registration_rejects_duplicate_and_code_changing_agents(self):
        registry = default_registry()
        with self.assertRaises(ValueError):
            registry.register(AgentCapability("ui", "Duplicate", "specialist", "Duplicate"))
        with self.assertRaisesRegex(ValueError, "human-approval"):
            registry.register(AgentCapability("developer", "Developer", "specialist", "Writes code", read_only=False))


class OAuthTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.client = Mock()
        cls.client.get_signing_key_from_jwt.return_value = SimpleNamespace(key=cls.private.public_key())

    def token(self, **changes):
        claims = {"sub": "fictional-user", "client_id": "fictional-client", "scope": SCOPE,
                  "iss": AUTH.issuer, "aud": AUTH.resource, "iat": int(time.time()), "exp": int(time.time()) + 300}
        claims.update(changes)
        return jwt.encode(claims, self.private, algorithm="RS256", headers={"kid": "test"})

    async def test_valid_signed_access_token(self):
        verifier = OAuthJWTVerifier(AUTH, jwks_client=self.client)
        access = await verifier.verify_token(self.token())
        self.assertEqual(access.resource, AUTH.resource)
        self.assertEqual(access.scopes, [SCOPE])

    async def test_noncanonical_discovery_identifiers_fail_before_startup(self):
        for resource, issuer in [
            ("https://h2o.example:443/mcp", AUTH.issuer),
            ("https://H2O.EXAMPLE/mcp", AUTH.issuer),
            (AUTH.resource, "https://identity.example"),
            (AUTH.resource, "https://IDENTITY.EXAMPLE/"),
        ]:
            with self.subTest(resource=resource, issuer=issuer), \
                 self.assertRaisesRegex(ValueError, "canonical"):
                OAuthConfig(resource, issuer, AUTH.jwks_url)

    async def test_wrong_issuer_audience_expiry_signature_and_algorithms_fail(self):
        verifier = OAuthJWTVerifier(AUTH, jwks_client=self.client)
        cases = [self.token(iss="https://other.example"), self.token(aud="other-resource"),
                 self.token(exp=int(time.time()) - 1), "invalid-token"]
        claims = jwt.decode(self.token(), options={"verify_signature": False})
        claims.pop("exp")
        cases.append(jwt.encode(claims, self.private, algorithm="RS256"))
        cases.append(jwt.encode(claims, None, algorithm="none"))
        claims["exp"] = int(time.time()) + 300
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cases.append(jwt.encode(claims, other, algorithm="RS256"))
        cases.append(jwt.encode(claims, "fictional-hmac-key-" * 3, algorithm="HS256"))
        for token in cases:
            with self.subTest(token_kind=token.split(".")[0]):
                self.assertIsNone(await verifier.verify_token(token))

    async def test_protected_http_auth_discovery_scope_and_origin(self):
        verifier = OAuthJWTVerifier(AUTH, jwks_client=self.client)
        app = create_http_app(ReviewService(default_registry()), AUTH, verifier=verifier)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://h2o.example") as client:
                response = await client.post("/mcp", json={})
                self.assertEqual(response.status_code, 401)
                self.assertIn("oauth-protected-resource/mcp", response.headers["www-authenticate"])
                metadata = (await client.get("/.well-known/oauth-protected-resource/mcp")).json()
                self.assertEqual(metadata["resource"], AUTH.resource)
                self.assertEqual(metadata["authorization_servers"], [AUTH.issuer])
                self.assertEqual(metadata["scopes_supported"], [SCOPE])
                headers = {"Authorization": "Bearer " + self.token(), "Accept": "application/json, text/event-stream"}
                request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
                response = await client.post("/mcp", headers=headers, json=request)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.json()["result"]["tools"]), 2)
                response = await client.post("/mcp", headers=headers, json={
                    "jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                        "name": "submit_review", "arguments": {"request": {"objective": TASK}},
                    },
                })
                self.assertTrue(response.json()["result"]["isError"])
                self.assertEqual(response.json()["result"]["structuredContent"]["code"], "reviews_disabled")
                response = await client.post("/mcp", headers=headers, content=b"x" * 65_537)
                self.assertEqual(response.status_code, 413)
                headers["Host"] = "untrusted.example"
                response = await client.post("/mcp", headers=headers, json=request)
                self.assertEqual(response.status_code, 421)
                headers.pop("Host")
                headers["Authorization"] = "Bearer " + self.token(scope="other:scope")
                response = await client.post("/mcp", headers=headers, json=request)
                self.assertEqual(response.status_code, 403)
                headers["Authorization"] = "Bearer " + self.token()
                headers["Origin"] = "https://untrusted.example"
                response = await client.post("/mcp", headers=headers, json=request)
                self.assertEqual(response.status_code, 403)

    async def test_unauthenticated_http_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            create_http_app(ReviewService(default_registry()), None)
        for resource in ("http://h2o.example/mcp", "https://h2o.example/other", "https://h2o.example/mcp?secret=x"):
            with self.subTest(resource=resource), self.assertRaises(ValueError):
                OAuthConfig(resource, AUTH.issuer, AUTH.jwks_url)


if __name__ == "__main__":
    unittest.main()
