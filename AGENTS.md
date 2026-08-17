# SIKSIK agent notes

Runtime truth is the code, especially `backend/app/services/sessions.py`, `backend/app/acquisition/`, and `android-agent/`. Dated facts live in `changelog` (newest first). Last code-verified pass: **2026-08-17**.

Stale: `Flow.md`, most `docs/*.md`, ios-media-puller READMEs, and changelog Phase 00–12 / Open Limitations.

Cursor rules (do not duplicate here):

- `.cursor/rules/siksik-code-authority.mdc` — always (pipeline, providers, mismatches)
- `.cursor/rules/siksik-android-bootstrap.mdc` — grants, install, handshake
- `.cursor/rules/siksik-android-acquisition.mdc` — inventory, social, selection, recovery
- `.cursor/rules/siksik-ios-acquisition.mdc`
- `.cursor/rules/siksik-analysis-report.mdc`
- `.cursor/rules/siksik-frontend.mdc`
- `.cursor/rules/siksik-code-rules.mdc` — coding standards

If code and these files disagree, fix the rule after verifying the function — never reverse the pipeline to match old markdown.
