YouTube Shorts Automation Platform

Round 2 — Outstanding Items on the Revised Implementation Plan

Reference: follow-up to “YouTube Shorts Automation – Clarification Questions and Change Requests” (Round 1) and the vendor's revised implementation plan.

Document version: 1.1   |   Status: All 7 items resolved by vendor — closed, with minor non-blocking follow-ups noted below

The revised implementation plan resolved 12 of the 16 items raised in Round 1, including a strong reworking of the delivery phasing. This document covers the remaining 7 items: 3 carried over from Round 1 that are still open or only partially addressed, and 4 new findings identified while reviewing the revision itself.

Carried Over From Round 1

ID

Item

Observation

Requested Action / Question

Vendor Response

N-01

Repo path contains a space

Still `d:/Projects/YT AUTOMATION/` in the revised repository structure — unchanged from Round 1.

Rename to a space-free path (e.g. yt-automation) before build begins.

M-08

CI present, but no CD

The GitHub Actions workflow covers linting and testing only. No deployment automation is described for promoting a passing build to staging/production.

Confirm whether deployment automation is deferred to Milestone 3 and will be described there, or add it now.

C-04

Semantic check is transcript-only

The added semantic_check.py evaluates the segment's transcript, hook, and payoff — it re-validates narrative coherence but does not inspect the finished render. Nothing currently verifies the subject stayed in frame, captions don't overlap or run off-screen, or the audio mix is intelligible.

Add a check (automated or via human review) that evaluates the finished render itself, not only the text behind it.

New Findings From Round 2 Review

ID

Item

Observation

Requested Action / Question

Vendor Response

R2-01

Duration limits defined in two places

The quality gate's technical check hardcodes duration bounds (15–60s, or configured max ≤180s) separately from clip-selection's MIN/MAX_SEGMENTS_PER_VIDEO config. A mismatch could cause the gate to reject clips that selection was configured to produce.

Confirm both read from a single shared configuration value, not independent constants.

R2-02

Encryption method as described is inaccurate

The plan describes “AES-128/256-CBC via cryptography.fernet.Fernet,” but Fernet's specification is fixed to AES-128-CBC + HMAC-SHA256 — it has no 256-bit option.

Confirm AES-128 (Fernet's actual guarantee) is sufficient, or specify a different implementation if AES-256 is genuinely required.

R2-03

Clip status naming inconsistency

The review-queue module describes states as WAITING_APPROVAL → APPROVED/REJECTED, but the Clip.status enum in the data model only defines QUALITY_CHECK — no WAITING_APPROVAL.

Reconcile the two: add WAITING_APPROVAL to the schema as a distinct state, or confirm it is meant to be synonymous with QUALITY_CHECK.

R2-04

Recommended LLM is deprecated

Gemini 1.5 Flash and 2.0 Flash are being retired (Google has announced a June 1, 2026 shutdown for 2.0 Flash/Flash-Lite); the current Flash lineup has moved well past this generation. Building against the recommended models would not work as of the current date.

Update the recommendation to a currently supported model, and make the specific model/version a swappable config value given how quickly this changes.

Summary

None of these 7 items are severe enough to block progress on their own, but R2-04 (deprecated LLM recommendation) should be corrected before any implementation work references a specific model, since the named models would not function as of the current date. The remaining items are small clarifications and consistency fixes that are cheapest to resolve now, before code is written against the mismatched assumptions.

Addendum — Final Round Resolution Status

All 7 items above were resolved in the vendor's final implementation plan and accompanying response matrix (dated September 10, 2026), including two items verified independently: the recommended model (gemini-3.8-flash) and its SDK/structured-output approach were confirmed current and correct, and the added render-level quality inspector (addressing C-04) was confirmed to meaningfully check the finished video, not just its transcript. The following four points are minor and non-blocking — worth a quick confirmation, not another formal round.

ID

Item

Observation

Requested Action / Question

Vendor Response

F-01

Repo root still contains a space

The `shorts_engine/` package itself is space-free, but the outer repository root shown in the structure (`d:/Projects/YT AUTOMATION/`) is unchanged — this is the actual build context for Docker and local tooling.

Confirm whether the outer folder will also be renamed, or whether containing the fix to the package name is considered sufficient.

F-02

Encryption key-derivation details incomplete

The AES-256-GCM design (PBKDF2-HMAC-SHA256 from MASTER_ENCRYPTION_KEY) doesn't specify how the salt is generated/stored, what iteration count is used, or how the key would be rotated if compromised.

Specify a unique stored salt and a current-standard iteration count; add a rotation process to the operations runbook (not needed before launch).

F-03

Segment-count upper bound not enforced in code

MAX_SEGMENTS_PER_VIDEO is commented “configurable up to 8,” but the shown Pydantic field has no upper-bound validation (le=8).

Add the constraint so the code matches the comment.

F-04

CD triggers before target environments exist

The CD workflow is defined starting Milestone 1, but the staging/production environments it deploys to aren't built until Milestone 3.

Confirm the CD stage is gated off or no-ops safely during Milestones 1–2 rather than failing on every push to main.

Recommendation

This plan is ready to move forward. The follow-ups above are the kind of detail normally closed out in a kickoff call or the first sprint rather than requiring further written sign-off.