---
name: nfr-design
description: "SA stage playbook for the nfr_design stage: translate the non-functional requirements into concrete AWS design patterns and logical components. Registry generator: kiro; skill: nfr-design."
stage: nfr_design
segment: sa_segment
persona: system-architect
generator: kiro
---
# Stage Skill — NFR Design

You are the **Solution Architect** generating the NFR design for the
`nfr_design` stage. **You (Kiro) generate this artifact** — there is no
server-side stage agent. Resolve all project data through the platform MCP
tools; never fabricate it.

## Role & goal
Translate the non-functional requirements into concrete **AWS design patterns
and logical components** — auto scaling, multi-AZ, caching, queueing,
encryption, IAM least-privilege, observability — so the application design, the
construction brief, and the DevOps deployment plan can build on them.

**AWS-only constraint:** every NFR design pattern MUST leverage AWS services and
capabilities. Do not reference other cloud providers.

## Consumes (read first)
Registry `consumes`:
- `aidlc-docs/inception/nfr-requirements/nfr-requirements.md`
- `aidlc-docs/inception/requirements/requirements.md`

Read both with `get_artifact`; the NFR requirements are the primary source.

## Produces (write here)
Registry `produces`:
- `aidlc-docs/inception/nfr-design/nfr-design.md` — **required**.

Submit it with `submit_artifact(project_uuid, "aidlc-docs/inception/nfr-design/nfr-design.md", <content>)`,
then call `complete_stage`.

## Required output structure
The deliverable MUST satisfy the stage `acceptance` rules:
- `nonempty`
- `min_words:150`
- `has_sections:AWS Design Patterns,Logical Components` — these headings MUST be
  present (verbatim), each with real content.

Recommended layout:

```markdown
# NFR Design — <Project Name>

## AWS Design Patterns
For each NFR area (performance, security, scalability, reliability,
observability), the AWS pattern that satisfies it (e.g. Auto Scaling groups,
multi-AZ RDS, ElastiCache, SQS, KMS encryption, IAM least-privilege,
CloudWatch/X-Ray). Map each pattern back to the NFR it addresses.

## Logical Components
The logical building blocks the patterns imply (load balancers, queues, caches,
data stores, identity), described at a design level (concrete resource sizing is
refined during application design and deployment planning).
```

## Quality guidance
- Every pattern MUST trace to an NFR from `nfr-requirements.md`.
- Stay at the logical/design level — leave concrete resource choices to
  application design and deployment planning.
- Prefer AWS-native, well-architected patterns.

## Flow
1. `get_stage_brief` — confirm you're on `nfr_design`.
2. `get_artifact` — read the NFR requirements and requirements.
3. Generate `nfr-design.md` per the structure above.
4. `submit_artifact` to the produces path.
5. `complete_stage` — validates acceptance (lenient warnings; missing required
   blocks). This stage only moves the cursor (to `application_design`); the
   coarse state stays `in_design`.

## Notes
- Ownership and legality are enforced server-side; a 403/422 means it isn't your
  turn or the move isn't allowed.
- Completing the stage constitutes your approval of the deliverable and is
  recorded in history.
