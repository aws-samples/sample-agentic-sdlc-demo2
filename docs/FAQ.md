# Agentic SDLC Transformation — FAQ

Companion to `TECHNICAL_BRIEF.md`. Written for a customer audience (technical and
non-technical). No secret values are included.

---

## General

**Q: What is this, in one sentence?**
A multi-persona, agent-driven platform that carries a software project through
its whole lifecycle — inception, design, construction, QA, deployment — with six
AI personas collaborating on one shared project under explicit human control,
driven from Kiro IDE.

**Q: Who are the "personas"?**
Business Analyst, Solution Architect, Software Developer, QA Engineer, DevOps
Engineer, and a read-only Maintenance Specialist. Each is an AI agent scoped to a
specific role and a specific slice of the workflow.

**Q: How does the architecture work at a high level?**
Kiro IDE is the only frontend. This build runs the Kiro-native
architecture: **Kiro generates every deliverable**, and a thin serverless backend
(Cognito + the Project API on Lambda/API Gateway + DynamoDB + S3 + CodeBuild) is
the authoritative system of record. There is no Amazon Bedrock AgentCore in the
active path — no Runtime, Gateway, or Memory, and no web UI. Kiro talks to the
backend through a Model Context Protocol (MCP) server.

**Q: What does "human control" mean here — is it fully autonomous?**
No. It's human-in-the-loop by design. Agents generate artifacts and propose next
steps, but a person approves each stage, reviews artifacts before advancing, and
performs every handoff. Nothing moves forward on its own except the automated
QA advance after successful code ingestion.

---

## Authentication & identity

**Q: How does a persona "log in"?**
The Platform MCP server signs the persona into Amazon Cognito non-interactively
using `USER_PASSWORD_AUTH` and receives ID and access tokens. In Kiro you select
a persona from the agent picker; each persona agent carries its own credentials,
so switching agents switches identity.

**Q: Where does a user's role come from?**
From the Cognito `custom:role` attribute set at sign-up. A post-confirmation
Lambda adds the user to the matching Cognito group. Group membership is what
drives all downstream authorization.

**Q: What Cognito client does the MCP server use?**
A single public **Kiro client** (`USER_PASSWORD_AUTH`) for the MCP server's
per-persona sign-in. (The former machine/client-credentials client and the
`agentcore` resource-server scope were removed with AgentCore — there are no
service-to-service Gateway calls anymore.)

**Q: The test users have known passwords in the stack outputs — isn't that insecure?**
Those six fixed test users are a **demo/workshop convenience** so each persona can
be exercised quickly. For any real deployment they would be removed and replaced
with self-service sign-up and per-user credentials. Call this out explicitly in
the demo.

**Q: Tokens seem to expire during a session — why?**
In the demo environment AWS credentials (used for S3 reads) come from a
time-limited source that expires roughly hourly. If a call returns HTTP 401,
refresh credentials and retry. Cognito tokens themselves are valid for 24h.

---

## Authorization & the workflow

**Q: How is "you can't act out of turn" actually enforced?**
The Project API is the authoritative gate. Every state-changing call is
owner-gated: your Cognito groups must contain the project's current `owning_role`,
verified from the JWT the API Gateway authorizer injects (unforgeable by the
client). If you're not the owner → HTTP 403.

**Q: What decides whether a state move is legal?**
A single `validate_transition` function in the shared workflow registry. It allows
only a state's defined forward step or its defined request-changes bounce.
Anything else → HTTP 422, and the record is left unchanged.

**Q: Can I skip a stage or jump the developer straight to deployment?**
No. Transitions are one forward step (or one request-changes step back). The
registry is the only place the graph is defined, so there's no path to skip.

**Q: What happens if I try to act as the wrong persona?**
You'll get a 403 (not the owner) or, for an illegal move, a 422. The agents don't
retry with a different action — you switch to the persona that owns the current
state.

**Q: How do "return for changes" bounces work?**
A reviewer (SA/QA/DevOps) can send the work one step back with required comments.
The comments are recorded and surface to the receiving persona via
`get_project` (`latest_change_request`). Bounces are never blocked by the review gate.

**Q: There are two enforcement "gates" — what are they?**
Local Kiro hooks: *review-before-approve* (you must read the artifacts before you
can advance/hand off) and *tests-before-submit* (a developer's local tests must
pass before submitting). These are guardrails on top of the authoritative
server-side RBAC.

---

## Kiro frontend & usage

**Q: Do I have to open the platform's source repo to use the personas?**
The agents are defined in a Kiro workspace's `.kiro/` config. They can be
project-scoped (in the repo) or promoted to global (`~/.kiro/`) so they work in
any folder. The MCP server code is referenced by absolute path, so it doesn't
need to live in your working folder. A helper script materializes a ready-to-use
test workspace in any folder.

**Q: How do I switch personas?**
Use the agent picker dropdown in the Kiro chat input. (Keyboard shortcuts exist
but can be intercepted by other bindings; the dropdown is the reliable way.)

**Q: Do the generated documents appear as files in my folder?**
No. Inception artifacts, the construction brief, and QA/DevOps reports live in
S3 and are viewed via the `get_artifact` tool (shown in chat). The only files
that land locally are what the Developer persona writes when coding.

**Q: Can I close Kiro and resume later?**
Yes. The project and its state are entirely server-side (in DynamoDB), and your
local spec/code persist on disk. Reopen the folder, pick your persona, and
continue. Submitted artifacts and completed stages are already saved server-side;
an artifact you were still drafting in Kiro is just re-generated and re-submitted.

**Q: Windows or macOS?**
Both. Setup differs slightly (PowerShell vs zsh/bash; the venv path is
`Scripts\python.exe` on Windows). See `USER_GUIDE.md` for platform-specific steps.

**Q: We hit `spawn ${AIDLC_MCP_PYTHON} ENOENT` — what is that?**
Kiro does not expand `${VAR}` in an MCP server's `command` field (only in the
`env` block). The `command` must be a literal absolute path to the venv Python.
The provided setup script writes this correctly.

---

## Artifact generation

**Q: What actually generates the artifacts?**
Kiro does. Each persona produces their stage deliverable with Kiro's own agent +
model, guided by the stage's Skill (`.kiro/skills/<persona>/<stage>`). There is no
server-side generation engine — the backend only governs state, RBAC, and
acceptance validation.

**Q: What's the generation flow?**
`get_stage_brief` returns the active stage's contract (its `produces`, `consumes`,
`acceptance`, and guiding `skill`). The persona reads the inputs and generates the
artifact in Kiro, uploads it with `submit_artifact` (an owner-gated,
`produces`-constrained short-TTL presigned PUT), then calls `complete_stage`,
which validates acceptance and advances the stage cursor.

**Q: Where does the agent's tool access come from?**
From the Platform MCP server (Project API + S3) and any additional MCP servers a
persona agent is configured with in Kiro. Atlassian (Jira/Confluence) is reached
via a proxy that resolves per-project credentials from Secrets Manager and calls
the Atlassian REST APIs directly.

**Q: Does the agent remember context across a session?**
Project state, the stage cursor, and history are server-side (DynamoDB), so any
persona resumes exactly where the project stands. Completed stages are recorded;
`complete_stage` advances the cursor so a persona only works the stages it still
owns.

---

## Git round-trip, QA & DevOps

**Q: How does developer code get back into the platform?**
The developer pushes to GitHub/GitLab, links the repo (`link_repo`), and submits.
Submitting triggers a CodeBuild job that clones the repo and syncs the source into
S3 (`{slug}/source/`), then auto-advances the project to QA.

**Q: Are private repos supported?**
Yes. An optional access token is stored in Secrets Manager per project and used
only by the ingestion job to clone. It is never stored on the record, logged, or
returned.

**Q: How are tests run?**
QA's `run_tests` starts a managed CodeBuild test-runner that pulls the ingested
source, auto-detects npm or pytest, runs the suite, writes a results artifact, and
reports pass/fail. QA decides whether to pass the project on or bounce it to Dev.

**Q: Is there automation on git push?**
Optionally. A dedicated public webhook verifies a GitHub HMAC / GitLab token
signature before any processing and re-triggers ingestion on a matching push.

**Q: What does the DevOps persona actually do today?**
It generates a deployment plan / release runbook and advances the project through
`in_deployment → deployed` (or returns it to QA). Deployment orchestration itself
is represented as planning + release artifacts in this build.

---

## Security & data

**Q: Is everything authenticated?**
Yes. The only network-exposed endpoint is the Project API (Cognito access-token
authorized), plus the signature-verified Git webhook, which checks a shared-secret
signature before doing anything. Any new network-exposed endpoint must be
authenticated.

**Q: Where is RBAC enforced?**
Entirely server-side in the Project API, from the caller's verified Cognito group
claim (owner-gate). There is no runtime component — the backend is the
sole enforcement point (403 non-owner / 422 illegal transition / 409 stale write).

**Q: How is project data isolated?**
Each project has its own S3 artifact prefix and a DynamoDB record keyed
`PROJECT#{uuid}`. The UUID is regex-constrained so a client value can never
traverse outside the reserved prefix. Presigned upload URLs are short-lived and
constrained to a path the active stage `produces`.

**Q: Where do secrets live?**
Amazon Secrets Manager: per-project Atlassian credentials, per-project Git tokens,
the Cognito app-client secret, and the webhook secret. All referenced by
name/ARN only.

---

## Operations & roadmap

**Q: How is it deployed?**
One AWS CDK v2 stack (`AIDLCPlatformWorkshop`) to a single pinned account in
`us-east-1`. The stack (Cognito + Project API + DynamoDB + S3 + CodeBuild) refuses
to deploy to a different account unless explicitly overridden — a guard against
cross-account accidents.

**Q: How does Kiro get its configuration?**
From the stack's `Kiro*` outputs — `KiroProjectApiUrl`, `KiroCognitoUserPoolId`,
`KiroCognitoClientId`, `KiroFilesBucket` — mapped into the MCP server's `AIDLC_*`
settings (`AIDLC_PROJECT_API_URL`, `AIDLC_COGNITO_USER_POOL_ID`,
`AIDLC_COGNITO_CLIENT_ID`, `AIDLC_FILES_BUCKET`, plus `AIDLC_AWS_REGION`,
`AIDLC_AUTH_MODE`, and per-persona credentials). There is no runtime ARN.

**Q: Does the deployed stack need Amazon Bedrock model access?**
No. Kiro performs generation with its own model, so Bedrock model access is not a
dependency of the deployed stack.

**Q: Is it multi-tenant / multi-project?**
Multiple projects are supported (each isolated by prefix). Personas are fixed
per user via Cognito groups. Broader multi-tenant isolation would be a follow-on.

**Q: What's intentionally not built yet?**
In-platform code-generation construction stages are disabled by design — the
developer codes externally (in Kiro) and the platform ingests the repo. The fixed
demo test users would be removed for production. Deeper deployment automation and
Jira/ServiceNow integrations are natural next steps.

**Q: Can we add a seventh persona or change the workflow?**
Yes, with a registry edit. Personas, segments, states, and transitions all derive
from one workflow registry (kept consistent between the Project API copy and the
Kiro MCP server copy). Adding a persona extends the data, not the code — though a
new segment's stages need a Kiro Skill authored under `.kiro/skills/` to produce
artifacts.
