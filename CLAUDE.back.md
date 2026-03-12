# CLAUDE.md

## Project Overview

Multi-Agent System (MAS) for rubric-based automated text evaluation and feedback.
System reads structured rubrics and unstructured text, outputs explainable scores and actionable feedback via multi-agent collaboration.

## Source of Truth — Read These First

Before executing ANY task, read the relevant source documents in this priority order:

| Priority | Document | Role | When to Read |
|----------|----------|------|--------------|
| **HIGHEST** | `docs/Zen.md` | Project constitution — overrides ALL conflicts | Every session start |
| HIGH | `docs/architecture.md` | Agent roles, state machine, data flow | Before implementing any agent, orchestrator, or pipeline code |
| HIGH | `docs/research.md` | System invariants, data contracts, config meta-schema, evaluation harness | Before defining contracts, configs, or evaluation logic |
| HIGH | `docs/plan.md` | Phased execution plan with per-task verification commands | Before starting any task |
| HIGH | `docs/Rubric_Guidelines.md` | Rubric core definition (dimensions, levels, descriptors) | When writing config artifacts only — NOT for code logic |
| HIGH | `docs/Adjudication_Rules.md` | Double/triple scoring, composite formula | When writing config artifacts only — NOT for code logic |
| LOW | `docs/Example.md` | Output format examples ONLY — NOT a rule source | When implementing feedback/explanation rendering |

**CRITICAL**: Always go to the source document. Do NOT rely on summaries in this file as your basis for implementation decisions. This file provides behavioral rules and guardrails, not specifications.

## Current Progress

- [x] Phase 0: Repository & Environment Setup
- [ ] Phase 1: Constitutional Contracts & Config Compiler   ← **CURRENT PHASE**
- [ ] Phase 2: Intermediate Data Contracts
- [ ] Phase 3: Mocked Orchestrator-StateGraph Baseline
- [ ] Phase 4: Policy-Aware MAS Wiring
- [ ] Phase 5: Real Provider Adapter & Prompt Wiring
- [ ] Phase 6: End-to-End Baseline Validation
- [ ] Phase 7: Evaluation Harness & Iteration Guardrails

## Execution Discipline

### Task Granularity
- Execute ONE task at a time from `docs/plan.md` (one checkbox item, not an entire Phase).
- After completing each task, IMMEDIATELY run its verification command from plan.md.
- Do not proceed to the next task until verification passes.
- Do not accumulate tasks and verify at Phase end.

### Phase Gate
- Do not start Phase N+1 until ALL exit conditions of Phase N are met.
- After completing a Phase, run the full Phase integration verification.

### Failure & Rollback Protocol
- If a verification command fails, fix within the scope of the current task.
- If fixes require more than 2 attempts on the same approach, STOP. Revert to the current Phase start point and re-implement from scratch per `docs/plan.md` Execution Rule #8.
- Never carry unverified temporary fields, scripts, or prompts into the next task.
- If any previously-passing test breaks, revert ALL changes from the current Phase and restore the last green state.

### Git Discipline
- Claude Code MUST NOT execute any git commands (commit, push, pull, rebase, checkout, etc.).
- After completing a task and passing its verification, Claude Code should report the result and prompt for manual commit.
- The human operator owns all git operations: commit granularity, message authoring, branch management, and rollback decisions.

## The Zero-Hardcoding Firewall

This is the single most important rule in this project. Violations require immediate Phase revert.

### Self-Check Before Writing Any Code

Before committing any file under `src/`, ask yourself:

1. Does this code contain ANY specific trait name (e.g., "Ideas and Content", "Organization", "Voice", "Word Choice", "Sentence Fluency", "Conventions") or trait code (I, O, V, W, S, C)?
2. Does this code contain ANY fixed score value (1, 2, 3, 4, 5, 6) or fixed scale range?
3. Does this code contain ANY specific composite formula, dimension weight, or threshold number?
4. Does this code contain ANY specific adjudication trigger pattern or cusp rule?
5. Does this code contain ANY display annotation string ("High", "Medium", "Low", "4-", "3-")?
6. Does this code reference or parse ANY `.md` rule source directly at runtime?

**If ANY answer is YES → the code is invalid. Refactor to read from `configs/` instead.**

### What Belongs Where

| Information | Belongs In | Never In |
|-------------|-----------|----------|
| Trait names, codes, count | `configs/rubrics/*.yaml` | `src/**/*.py` |
| Score scale, levels, descriptors | `configs/rubrics/*.yaml` | `src/**/*.py` |
| Adjudication thresholds, triggers | `configs/policies/adjudication/*.yaml` | `src/**/*.py` |
| Composite formula, weights | `configs/policies/aggregation/*.yaml` | `src/**/*.py` |
| Explanation templates, citation rules | `configs/policies/explanation/*.yaml` | `src/**/*.py` |
| Display annotations | `configs/` (display overlay config) | `src/**/*.py` |
| Prompt text | `configs/prompts/*.yaml` (Jinja2 templates) | `src/**/*.py` |

## Architectural Boundaries — Do Not Cross

### Contract-First Data Flow
- ALL data passed between agents/nodes MUST use types defined in `src/contracts/`.
- Creating ad-hoc dicts, tuples, or undeclared fields to pass data between nodes is FORBIDDEN.
- If a needed field does not exist in contracts, define it there first with a unit test, then use it.

### Orchestrator Boundary
- The orchestrator (`src/orchestrator/`) owns state transitions, dispatch, and routing.
- Agents (`src/agents/`) are stateless workers that receive typed input and return typed output.
- No agent may directly invoke another agent. All routing goes through the orchestrator.
- No free-form conversational agent chains. The system is a state machine, not a chat.

### Provider Boundary
- `src/providers/` handles ONLY: LLM API calls, request formatting, response parsing, retry, timeout.
- `src/providers/` must NOT contain: rubric semantics, adjudication logic, aggregation formulas, explanation policy, state machine routing.
- `src/policies/` consumes provider output but never calls providers directly.

### Config-Runtime Boundary
- At runtime, the system reads ONLY from `configs/` artifacts (YAML/JSON).
- `docs/*.md` files are human documentation. They are NEVER parsed or loaded at runtime.
- `docs/Rubric_Guidelines.md` and `docs/Adjudication_Rules.md` inform what goes INTO `configs/`, but are not themselves config sources.

## Mock-First Development

- `mock` mode must be stable, deterministic, and reproducible BEFORE any real LLM provider is connected.
- After real provider integration, ALL mock tests must continue to pass unchanged.
- The `MockProvider` returns deterministic fixtures — it does not call any external API.
- `--provider mock` and `--provider real` are the only two modes; switching between them must not require code changes in `src/agents/`, `src/policies/`, or `src/orchestrator/`.

## Testing Requirements

- Write test FIRST → implement → verify. Follow RED-GREEN-REFACTOR.
- Every contract in `src/contracts/` must have:
  - Schema validation test
  - Roundtrip serialization/deserialization test
  - No-extra-fields test (reject undeclared fields)
- Integration tests must verify policy-aware pipeline behavior.
- E2E tests must cover: normal path, adjudication path, fallback/re-extract path, terminal validation.
- Golden snapshot tests protect baseline from regression.

## Language Requirements

All responses, explanations, and reasoning (including internal thinking) MUST be written in **Simplified Chinese (简体中文) and English**.
- Use Simplified Chinese as the primary language for all communication and explanations.
- Code, identifiers, file paths, and command-line output remain in English.
- When explaining code or decisions, write in Simplified Chinese with English technical terms inline where appropriate.

## Out of Scope for MVP

UI, monitoring dashboards, caching layers, database persistence, async queues, multi-model routing optimization. Do not implement these.
