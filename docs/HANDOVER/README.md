# Handover Package (R4-7.1)

This directory contains the full handover package for the YT-Shorts
Automation Platform, designed for a software company picking up the
codebase.

## Contents

| File | Purpose |
|------|---------|
| `architecture.md` | High-level architecture with Mermaid C4 diagrams. |
| `data-model.md` | ERD-style data model (auto-generated from models.py). |
| `api-reference.md` | Public HTTP API reference (auto-generated from OpenAPI). |
| `module-map.md` | What each module does, where to start reading. |
| `traceability.md` | Requirement → code → test traceability (from the ledger). |
| `dev-setup.md` | Dev setup for Windows and Linux (verified on a clean runner). |
| `test-strategy.md` | How to run each test suite; what each suite covers. |
| `release-process.md` | How to cut a release; upgrade / rollback; smoke. |
| `runbook.md` | Day-2 operations: backup, restore, key rotation, health. |
| `decision-log.md` | Index of architectural decisions. |
| `coding-standards.md` | Style, lint, type-check, security. |
| `secrets-handling.md` | How secrets are stored; how to rotate. |
| `known-limitations.md` | Known limitations and roadmap. |
| `sbom.md` | Software bill of materials + license list. |
| `acceptance-criteria.md` | Acceptance criteria + demo script. |
| `maintenance.md` | Maintenance expectations. |
| `glossary.md` | Domain glossary. |

## How to read this package

* **First time on the project:** start with `architecture.md`, then
  `module-map.md`, then `data-model.md`, then `traceability.md`.
* **Setting up a dev machine:** `dev-setup.md` (both OSes), then
  `test-strategy.md`.
* **Cutting a release:** `release-process.md` + `runbook.md` for
  pre-release checks.
* **On-call / operations:** `runbook.md` + `secrets-handling.md` +
  `maintenance.md`.

Every file in this directory is part of the source tree. Treat edits
the same way as edits to the ledger: the ledger is the source of truth
for status; the handover is the source of truth for the codebase.
