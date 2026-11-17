# GAMGEE Cloud — Technical Handbook

**Edition:** 1.0 | **Status:** Engineering preview | **Audience:** Architects, operators, developers and AI engineers

GAMGEE is an outcome-oriented cloud operating layer. Users declare what they want to run; GAMGEE validates that intent and reconciles it through controlled infrastructure drivers. This handbook explains the motivation, architecture, technology, installation, compute, AI, APIs, MCP, security and operations.

> **Implementation honesty:** GAMGEE is an active engineering preview. Each chapter distinguishes implemented capabilities from foundations and roadmap items. Planned functionality must never be presented as operational.

## Table of contents

| Chapter | Subject |
|---|---|
| [01 — Introduction](introduction.md) | Motivation, principles, product boundaries and terminology |
| [02 — Architecture](architecture.md) | Control Tower, Resource Engine, cells, workspaces and execution flows |
| [03 — Technology stack](technology-stack.md) | Components, responsibilities and design rationale |
| [04 — Installation](installation.md) | Host preparation, guided install, first workspace and deployment |
| [05 — Compute and scaling](compute-and-scaling.md) | Minimum-to-large footprints, servers, scale-up and scale-out |
| [06 — AI Factory](ai-factory.md) | Models, RAG, agents, workflows, AI usage and governance |
| [07 — API, integrations and MCP](api-integrations-mcp.md) | Resource API, integrations, MCP architecture and tool security |
| [08 — Security and operations](security-operations.md) | Trust boundaries, monitoring, backups, upgrades and readiness |

Existing detailed references: [Architecture](architecture.md), [Control Tower Engineering](control-tower-engineering.md), [GAMGEE Cloud OS](gamgee-cloud-os.md).

## Reading paths

- New users: 01 → 02 → 03 → 04.
- Platform operators: 02 → 04 → 05 → 08.
- Developers: 02 → 03 → 07.
- AI engineers: 03 → 06 → 07 → 08.

## Capability labels

- **Implemented** — present in the current repository and exposed by the implementation.
- **Foundation** — an upstream component or partial integration exists; the complete lifecycle may not.
- **Planned** — target design or roadmap item; do not assume it can be provisioned today.
- **Not production-ready** — known gaps prevent a production assurance claim.

Keep these documents aligned with API schemas, driver registry, installation catalogue, Compose files, Ansible profiles and tests. Update the relevant chapter whenever implementation changes.
