# Task #6: ChatGPT integration foundation

Research and local validation date: November 16, 2026.

This is an MCP resource server, not a hosted deployment, a new agent framework,
or an automatically installed ChatGPT connection. It exposes two read-only tools:

| Tool | Input | Output |
| --- | --- | --- |
| `get_agent_capabilities` | `{}` | Registry-backed agents, modes, permission boundaries, billing and limits. |
| `submit_review` | `{"request":{"objective":"Review a fictional frontend…","mode":"description"}}` | Lead recommendation, specialist findings, optional source metadata, and a human-quality-review flag. |

`mode` is `description` or `sample_codebase`. The latter reuses Task #4's existing
reader, fixed fictional files, limits, and citation checks. MCP does not expose
that reader directly, accept paths/URLs/repository names, or add a second reader.
There are no code-changing tools. The existing CLI and live-validation entry
points remain unchanged.

## Current ChatGPT requirements and primary sources

OpenAI's current documentation supports direct custom MCP connections without a
custom UI or marketplace package. A hosted endpoint uses Streamable HTTP, usually
at `/mcp`. An approved Secure MCP Tunnel is an alternative for private servers;
no tunnel or hosting is provisioned by this project. See
[Build an MCP server](https://developers.openai.com/plugins/build/mcp-server) and
[Connect and test your plugin](https://developers.openai.com/plugins/deploy/connect-chatgpt).

The current Plugins experience uses **Plugins → plus button → Add custom MCP
server**. It does not require the old Developer Mode step first. Older/legacy
workspace interfaces may still use Apps settings and developer-mode controls.
Account/workspace policies and admin permissions determine availability; current
help documents Business/Enterprise/Edu creation permissions and limited Pro
read/fetch access. Check the controls actually available to your account; this
repository does not grant them. See
[Connecting custom MCP servers in ChatGPT](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt).

For private deployment, the MCP server is an OAuth resource server, while an
operator-provided identity provider issues access tokens. ChatGPT needs protected
resource discovery, authorization-server discovery, authorization-code flow with
PKCE S256, audience/resource binding, and a compatible OAuth client registration
method. Supported registration approaches include a predefined client, CIMD, or
DCR; configure one supported by your provider and ChatGPT's connection flow. See
[OpenAI authentication guidance](https://developers.openai.com/plugins/build/auth)
and the [MCP authorization specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization).

Search/fetch tools are not required for an ordinary custom MCP connection. This
foundation does not implement company-knowledge search, an interactive widget,
legacy GPT Actions/OpenAPI manifests, or public marketplace packaging.

## Architecture and extension points

```text
ChatGPT / local MCP client
        ↓
MCP protocol + OAuth (HTTP only)
        ↓
ReviewService: request validation, billing gate, limits, safe results
        ↓
AgentRegistry → configured manager runner
        ↓
existing h2o.review → Lead → specialist tool(s) → Lead
```

- `h2o_integration/server.py`: MCP schemas, annotations, argument boundary,
  structured responses, protected HTTP app. It does not enumerate agent IDs or
  contain agent prompts.
- `h2o_integration/service.py`: transport-independent registry/service and the
  adapter that calls the existing Agents SDK implementation. `ReviewResult`
  uses the generic `specialist_reviews` name rather than binding transport to UI.
- `h2o_integration/auth.py`: configured issuer/JWKS/audience validation. This is
  not a homegrown authorization server or token-issuance service.
- `h2o.py` and `sample_codebase.py`: existing agent/reader implementation, reused
  without modifications in this PR.

To add Architecture, Developer, or QA later:

1. Implement a specialist module and wire its `Agent.as_tool()` into an approved
   manager workflow, preserving that workflow's permissions and tests.
2. Register its implemented, read-only `AgentCapability` in trusted application
   code. Supply or replace the manager's runner if the SDK workflow changes.
3. Return the transport-independent `ReviewResult`, then test discovery,
   delegation, errors, and permissions. MCP tool definitions need no agent-count
   change; capability discovery reads the registry.

A registry entry is metadata, not an agent implementation; registration alone
does not make the existing Lead delegate to a new specialist. Only Lead and UI
are currently implemented. Remote requests cannot register agents, import modules,
select arbitrary runners, or change the backend model.

Registration rejects agents declared to change code. Future code-changing work
needs a separately reviewed permission model, a trusted human approval mechanism,
and correctly annotated new tools. A caller-supplied `approved: true` is not an
approval mechanism and is rejected by this foundation's input schema.

## Local setup and non-billable verification

This branch is stacked on PR #3's `feature/read-only-sample-codebase` because PR #3
was still open at implementation time. Review/merge that dependency first, then
retarget this PR to `main` and verify its diff before merging. Do not merge either
automatically.

From this checkout:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip check
python -m unittest discover -s tests -v
python -m h2o_integration --help
python -m h2o_integration --transport stdio
```

The last command waits for MCP protocol messages on stdin; it is not a text-chat
prompt. A local MCP client/Inspector can launch that command and initialize,
list tools, and call `get_agent_capabilities`. Do not send ordinary text to stdin.
Protocol traffic uses stdout; do not add diagnostic `print()` statements there.
No API key is needed for capability discovery, and billable reviews default to
disabled even when a backend key exists. Stdio trusts the local OS user/process;
it is not an anonymously exposed remote endpoint.

To enable actual backend reviews privately, set `OPENAI_API_KEY` and
`H2O_MCP_ENABLE_REVIEWS=1` in this checkout's `.env` or deployment secret settings.
`OPENAI_MODEL` is operator-controlled and defaults to `gpt-4.1-mini`. Never paste
credentials into ChatGPT, tool arguments, a server URL, or version control.
The backend key and OAuth access tokens are separate credentials; MCP tokens are
never forwarded to OpenAI or included in agent prompts/results.

Both MCP tools declare `readOnlyHint: true`, `destructiveHint: false`, and
`openWorldHint: false`: they provide bounded review computation, not arbitrary
network search or external writes. Review computation still sends the supplied
fictional objective to the configured OpenAI backend and incurs API billing.
Annotations do not replace server-side authorization.

## Approved HTTPS deployment: operator-provided configuration

Nothing below provisions infrastructure, modifies DNS, publishes a plugin, or
creates ongoing hosting charges. Choose and approve the host/provider first.

You must provide:

| Setting or requirement | Owner-supplied value |
| --- | --- |
| Hosting/TLS | Approved process/container host and reverse proxy with a valid HTTPS certificate. |
| Backend billing | OpenAI API key/project with model access and appropriate billing controls. |
| Resource | `H2O_MCP_RESOURCE_URL=https://approved-host.example/mcp` (exact audience, no trailing slash/query). |
| OAuth issuer | `H2O_MCP_OAUTH_ISSUER=https://approved-identity.example/` (copy the exact canonical issuer from provider discovery, including any tenant path). |
| Signing keys | `H2O_MCP_JWKS_URL=https://approved-identity.example/keys` (trusted provider's JWKS, never caller input). |
| Access policy | IdP grants the dedicated `h2o:review` scope only to approved users/clients. |
| OAuth client | Provider metadata and PKCE S256; configure predefined client, CIMD or DCR, plus exact ChatGPT callback URI(s). |
| ChatGPT permissions | Account/workspace permission to add and use custom MCP connections; admin publication if applicable. |

The identity provider must issue **RS256-signed access tokens** containing `sub`,
`iss`, `aud`, `iat`, and `exp`, with a space-delimited `scope` including
`h2o:review`. This small implementation does not accept opaque tokens, symmetric
JWTs, other algorithms, or ID tokens without the required access scope. The exact
resource URL must appear in `aud`. Configure the provider to preserve OAuth's
`resource` parameter. Use callback URIs shown by the actual ChatGPT client flow;
do not invent them or allow wildcard redirects. Configure refresh-token support
if your organization needs it, following its provider policy.

Start one backend process on a private/loopback interface behind the approved
HTTPS proxy:

```bash
python -m h2o_integration --transport streamable-http --host 127.0.0.1 --port 8000
```

HTTP startup fails without all OAuth settings; there is no anonymous-HTTP bypass.
Resource/issuer values must already be canonical (lowercase host, no explicit
default `:443` port, and the issuer's exact trailing slash). Noncanonical values
fail at startup so discovery cannot advertise a different JWT audience/issuer.
Do not rewrite an identity provider's issuer arbitrarily; use the exact value
from its discovery metadata and verify that it is accepted before deployment.
`--host 0.0.0.0` is available for an approved private container network, not a
recommendation to publish the raw HTTP port.

Proxy `/mcp` and `/.well-known/oauth-protected-resource/mcp` unchanged. Preserve
the canonical `Host` and `Authorization` headers, disable response buffering for
MCP, allow a request timeout exceeding the 180-second workflow limit, and enforce
the 64 KiB body limit. Do not log bearer tokens, prompts, source text, or response
bodies. Built-in access logging is disabled; configure safe gateway metrics using
status/error codes only. The app does not trust forwarded headers. Keep the raw
backend port private and validate HTTPS from the external client.

Protected-resource metadata is intentionally public for discovery. `/mcp`
requires a valid token and the scope on every request, including discovery of the
tool list. Missing/invalid credentials return 401 with a metadata challenge;
missing scope returns 403. Tokens with the wrong issuer, audience, signature,
algorithm, or expiry are rejected. JWKS is fetched only from the operator's fixed
HTTPS URL, with a timeout/cache; token-supplied key URLs are not trusted.

DNS-rebinding protection remains enabled. The canonical configured host and
loopback hosts are accepted; unexpected Host/Origin headers are rejected. This
is a server-to-server ChatGPT endpoint, not a wildcard-CORS browser API.

## Register and test in ChatGPT, after deployment approval

1. Verify protected HTTP initialization/tool discovery with an approved MCP
   client. Test 401, insufficient-scope 403, valid calls, and safe errors first.
2. Open ChatGPT **Plugins**, select **+ → Add custom MCP server**, supply a name
   such as “PAGENTWORKFLOW Reviews,” and enter the approved HTTPS `/mcp` URL.
   If your organization uses Secure MCP Tunnel, configure that approved path
   according to its workspace controls instead; this task creates no tunnel.
3. Configure OAuth with your identity provider, complete the login/consent flow,
   review the risk warning, and select **Create as a plugin**.
4. Confirm exactly `get_agent_capabilities` and `submit_review` are discovered,
   with read-only annotations and the documented schemas. If these controls are
   absent, ask the workspace owner to check plan/RBAC policy; do not bypass it.
5. Select/@mention the integration in a new chat and test the examples below.
   After changing server tool metadata, refresh the connection and retest in a
   new conversation. Workspace-wide publication is a separate admin decision.

No real ChatGPT account connection or hosted OAuth-provider flow was exercised
during local implementation; those require the operator-provided configuration.

Example ChatGPT requests:

- “Using PAGENTWORKFLOW Reviews, list the available agents and review modes.”
- “Ask the Lead to review this fictional mobile workshop planner: its menu uses
  unlabeled icons and its form has placeholder-only labels. Return priorities
  and acceptance checks. Use description mode.”
- “Ask the Lead to review the fixed fictional Lantern Board sample codebase.
  Use sample_codebase mode and preserve file/line citations.”
- Negative case: “Edit app.js, run a shell command, or inspect another repo.”
  The integration has no such tool or permission; it can only recommend changes.

## Limits, errors, and verification

Requests reject extra fields, unsupported modes, empty/oversized objectives,
obvious API-key strings, the configured backend key, and HHC-named requests. These
checks cannot classify all sensitive prose; use fictional demonstration data
only. No internal directories, arbitrary files, URLs, or repositories are fetched.

Limits: one active review, three starts/minute **per process**, 8,000 objective
characters, 180 seconds/workflow, 1,200 output tokens/model request, no automatic
backend retries. Tracing is disabled and Responses API storage is disabled for
this adapter. These are not dollar spending caps; failed/time-out calls can still
be billable. For multiple workers/instances, an approved gateway must enforce
shared quotas. No durable sessions, job queue, database, or conversation log is
added; restarts reset the in-memory request limit.

Results have `ok`, `code`, `message`, and `data`; failures set MCP `isError` and
return stable, sanitized messages without provider exception bodies. Codes
include `invalid_request`, `unsupported_tool`, `forbidden`, `reviews_disabled`,
`backend_configuration`, `restricted_input`, `busy`, `rate_limited`,
`backend_authentication`, `backend_limit`, `backend_unavailable`, `source_access`,
`delegation`, `response`, and `review_failed`. Neither secrets nor approval flags
are returned as authority for future operations. Generated recommendations still
need human technical review; valid citations do not prove recommendation quality.

Run `python -m unittest discover -s tests -v`. Tests cover the original CLI/live
workflow, actual MCP stdio initialization, schema/tool registration, structured
request handling, sanitized failures, bounded real SDK source review using
scripted models, dynamic capability registration, rejection of mutating agents,
backend limits/settings, signed JWT validation, HTTP discovery/challenges/scope,
Host/Origin/body boundaries, and disabled-review behavior. All automated tests
are offline and make no paid API requests or external IdP requests.

Implementation validation: **58 tests passed** (42 existing unchanged tests and
16 new integration tests), on Linux/Python 3.12.14 with Agents SDK 0.24.0 and MCP
SDK 2.3.0. Dependency consistency, Python 3.10 syntax, credential scans, and diff
checks passed. Python 3.10 was not runtime-tested. No new live model calls,
public/tunnel deployment, real identity-provider login, or actual ChatGPT
connection was performed. Those remain explicit operator setup/acceptance work.

Task #7 review added a discovery/JWT-identifier regression fix: accepted URL
spellings containing `:443`, uppercase hostnames, or a missing issuer-root slash
could previously diverge from the MCP SDK's normalized metadata and reject valid
tokens. Startup now rejects these mismatches. The updated suite has **59 passing
offline tests**, including this new regression and the published scope metadata.

## Task #7: proposed private deployment (not authorized or deployed)

Recommended initial topology: one managed Render Python web-service instance and
a dedicated Auth0 tenant. This avoids a VM to patch, a database, a custom login
server, custom-domain purchases, and autoscaling. Access is private by OAuth even
though the MCP HTTPS endpoint is internet-reachable; this is not private-network
hosting.

As checked November 16, 2026, [Render's pricing](https://render.com/pricing)
lists 0.5 CPU/512 MB at $7/month on a $0 Hobby workspace, and
[Auth0 Free](https://auth0.com/pricing) lists $0/month with capacity well above two
users. Budget approximately $7/month plus OpenAI API usage and any applicable
provider overages/tax. Reconfirm current plan entitlements before purchase.
Render's [free instances sleep after 15 minutes and can take about a minute to
restart](https://render.com/docs/free), which can disrupt interactive MCP use.

Proposed hosting settings, to prepare locally before any external action:

- One instance/worker; no database, autoscaling, or automatic deploys.
- Use the included `onrender.com` hostname and managed TLS initially.
- Build: `python -m pip install -r requirements.txt`.
- Start: `python -m h2o_integration --transport streamable-http --host 0.0.0.0 --port "$PORT"`.
- Use Render's default TCP health check initially, not an unauthenticated call to
  the protected `/mcp` tool endpoint. A later health endpoint can expose only a
  constant readiness status, without secrets or backend API calls.
- Preserve Host/Authorization at the edge. Keep `H2O_MCP_ENABLE_REVIEWS=0` during
  authentication acceptance tests; set the backend key only in secret settings.

Proposed OAuth settings:

1. Create a dedicated tenant/connection, disable public signup, and provision only
   the first authorized user. Enable only that connection for the application.
2. Register an RS256 custom API with the exact canonical HTTPS `/mcp` resource
   URL as its identifier and a single `h2o:review` permission. Enable API RBAC and
   grant that permission only to approved users. Without RBAC, Auth0 can include
   requested API scopes regardless of user assignments; see
   [Auth0's API RBAC guidance](https://auth0.com/docs/manage-users/access-control/configure-core-rbac/enable-role-based-access-control-for-apis).
3. Use a pre-registered authorization-code/PKCE S256 OAuth client supported by
   ChatGPT, with the exact callback URI shown in its connection flow. No broad
   DCR/CIMD onboarding is needed for this initial single-client setup.
4. Enable Auth0's [Resource Parameter Compatibility Profile and Include Issuer
   in Authorization Responses](https://auth0.com/ai/docs/mcp/guides/resource-param-compatibility-profile).
   Confirm that `resource` produces the exact required token audience.
5. Copy the canonical issuer and JWKS URL from provider discovery. Use short
   access-token lifetimes (for example, ten minutes). If persistent login is
   desired, enable offline access, request `offline_access`, allow refresh tokens,
   and enable refresh-token rotation. The real connection must exercise refresh.
6. Add a second user later only after owner approval: provision their identity,
   grant the same review permission, and enable their ChatGPT app access. Do not
   share the first person's tokens. Blocking a user/revoking refresh tokens does
   not instantly invalidate an already-issued JWT; it remains valid until expiry.

The server delegates eligibility to the IdP's signed scope grants; it has no
separate user database or subject allowlist. Closed signup and correctly assigned
API permissions are therefore deployment requirements, not UI-only preferences.

Codex can prepare code fixes, regression tests, a deployment manifest with secret
placeholders, startup commands, and acceptance scripts without external access.
Creating/configuring provider accounts, authorizing repository access, accepting
terms/billing, publishing the endpoint, or adding users requires explicit owner
authorization and appropriate scoped connections. The owner supplies credentials
through private secret settings and enables ChatGPT account/workspace permissions.
No such external changes were performed for this plan.

Remaining rollout gates: owner approval of PR merges and provider/budget choice;
actual hosting/IdP/account configuration; and a real ChatGPT discovery → login/
consent → PKCE code exchange → scoped tool call → refresh/revocation acceptance
test. Local tests verify resource-server behavior, not that external flow.
Only after those pass should the owner authorize billable reviews. Process-local
rate limits reset on restart and do not form a hard dollar cap; keep one instance
and use a dedicated backend project with agreed usage/billing controls.
