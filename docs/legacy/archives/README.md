# Legacy archive

This directory preserves superseded, archived, and non-reusable artifacts that
were removed from the repository root during structural cleanup.

Purpose:
- Keep the repository root presentable and focused on the current project
  surface.
- Retain historical material in a documented, inspectable place rather than
  deleting it outright where it may still be useful for reference.

Contents:
- `structure-shims/` — archived directories that mirrored old service layouts
  but were not the canonical live code. Retain for reference; the live service
  module layout is governed separately.
- `shorts-engine-control-center/` — legacy Windows one-click control-center
  launcher batch scripts. Archived, not part of the presentable repo surface.
- `in-progress-dev/` — legacy Windows setup launcher/scripts and a setup log
  captured during a prior setup run. Archived as provisioning debris.

Notes:
- This directory is for records and reference. If an archived item becomes
  genuinely reusable, promote it to its proper canonical home with the
  necessary import/dependency alignment before reintroducing it.
