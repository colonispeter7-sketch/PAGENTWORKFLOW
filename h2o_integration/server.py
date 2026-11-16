"""MCP transport only: two stable tools over stdio or protected Streamable HTTP."""

from __future__ import annotations

import logging
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict, ValidationError
from urllib.parse import urlsplit

from .auth import OAuthConfig, OAuthJWTVerifier, SCOPE
from .service import ReviewRequest, ReviewService, ToolResponse, failure


class SubmitArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request: ReviewRequest


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def result(response: ToolResponse) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=response.message)],
                          structured_content=response.model_dump(mode="json"), is_error=not response.ok)


class ToolBoundary:
    """Reject arguments before SDK validation can echo caller data in errors."""

    def __init__(self, authenticated: bool):
        self.authenticated = authenticated

    async def __call__(self, context, call_next):
        if context.method == "tools/list":
            response = await call_next(context)
            for tool in response["tools"]:
                tool["inputSchema"]["additionalProperties"] = False
            return response
        if context.method != "tools/call":
            return await call_next(context)
        params = context.params
        if not isinstance(params, dict):
            return result(failure("invalid_request", "Tool calls require named JSON arguments."))
        schemas = {"get_agent_capabilities": NoArguments, "submit_review": SubmitArguments}
        name = params.get("name")
        if not isinstance(name, str) or name not in schemas:
            return result(failure("unsupported_tool", "Only capability discovery and read-only reviews are supported."))
        if self.authenticated:
            token = get_access_token()
            if token is None or SCOPE not in token.scopes:
                return result(failure("forbidden", "A valid scoped OAuth access token is required."))
        try:
            arguments = params.get("arguments", {})
            if not isinstance(arguments, dict):
                raise ValueError("Arguments must be an object.")
            schemas[name].model_validate(arguments)
        except (ValidationError, TypeError, ValueError):
            return result(failure("invalid_request", "Check the tool schema: bounded objective, supported mode, and no extra fields or credentials."))
        try:
            response = await call_next(context)
            if isinstance(response, CallToolResult) and response.structured_content:
                response.is_error = response.structured_content.get("ok") is False
            elif isinstance(response, dict) and response.get("structuredContent"):
                response["isError"] = response["structuredContent"].get("ok") is False
            elif isinstance(response, dict) and response.get("isError"):
                return result(failure("request_failed", "The request failed; no validated result is available."))
            return response
        except Exception:
            return result(failure("request_failed", "The request failed; no validated result is available."))


def create_server(service: ReviewService, *, oauth: OAuthConfig | None = None,
                  verifier=None) -> MCPServer:
    auth = None if oauth is None else AuthSettings(
        issuer_url=oauth.issuer, resource_server_url=oauth.resource,
        required_scopes=[SCOPE], validate_token_resource=True,
    )
    server = MCPServer(
        "PAGENTWORKFLOW", version="0.1.0", log_level="WARNING", debug=False,
        instructions=("Use fictional frontend data only. Discover capabilities, then submit a read-only "
                      "review to the Lead. Reviews incur backend API usage. No code-changing, shell, "
                      "browser, or arbitrary-file tools exist. Never supply credentials or HHC data."),
        auth=auth, token_verifier=(verifier or OAuthJWTVerifier(oauth)) if oauth else None,
        middleware=[ToolBoundary(authenticated=oauth is not None)],
    )
    security = [{"type": "oauth2", "scopes": [SCOPE]}] if oauth else [{"type": "noauth"}]

    @server.tool(
        title="Available H2O agent capabilities", structured_output=True,
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False,
                                    idempotent_hint=True, open_world_hint=False),
        meta={"securitySchemes": security},
    )
    async def get_agent_capabilities() -> ToolResponse:
        """List registered agents, supported review modes, read-only boundaries and billing limits."""
        return service.capabilities()

    @server.tool(
        title="Submit a read-only review to the Lead Agent", structured_output=True,
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False,
                                    idempotent_hint=False, open_world_hint=False),
        meta={"securitySchemes": security},
    )
    async def submit_review(request: ReviewRequest) -> ToolResponse:
        """Review fictional frontend descriptions or the fixed fictional sample; returns specialist findings and the Lead recommendation. Uses billable OpenAI API compute, makes no code changes, and cannot access arbitrary repositories or URLs."""
        return await service.submit(request)

    return server


def create_http_app(service: ReviewService, oauth: OAuthConfig, *, verifier=None):
    # Auth is mandatory on HTTP. stdio is a separate, local-OS trust boundary.
    if oauth is None:
        raise ValueError("HTTP cannot run without OAuth configuration.")
    server = create_server(service, oauth=oauth, verifier=verifier)
    # The SDK otherwise logs rejected Host/Origin values verbatim. Record only
    # sanitized status codes at an approved gateway, not raw peer headers here.
    logging.getLogger("mcp.server.transport_security").setLevel(logging.CRITICAL)
    endpoint = urlsplit(oauth.resource)
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[endpoint.netloc, "127.0.0.1:*", "localhost:*", "[::1]:*"],
        allowed_origins=[f"https://{endpoint.netloc}"],
    )
    return server.streamable_http_app(
        stateless_http=True, json_response=True, max_request_body_size=65_536,
        transport_security=security,
    )
