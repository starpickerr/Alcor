# Full audit repair

- Authorized: repair the retained Legacy CI without disabling tests; commit each verified phase and push only the repair branch. No main merge, personal Profile or official DSH changes.
- Baseline: `8b4b3a71f054b6d75ec924aaf2a24447a6626c7f`; clean existing worktree `.local/official-shared`, branch `fix/full-audit-20261112`.
- Input evidence: `/tmp/alcor-main-legacy-postmerge-failed.log` (140 failures, 33 errors; UI group timeout).
- Companion working repos remain unchanged. Only small fixed source checkouts may be prepared for the existing suite; reuse the existing Stella interpreter.

## Phases

1. Fix companion revisions, CI layout and test interpreter. Check all eight plugin digests and both MCP digests; keep tamper rejection. The user explicitly authorized replacing the unreproducible eLearning Legacy digest with the reviewed clean `cfcd286` tree. Native product source locks remain unchanged.
2. Retain real macOS Keychain checks and Linux denial/security behavior; repair fixture/platform arrangement and missing test dependencies/local-origin fixture.
3. Run all 18 UI scripts with per-script start/end/duration and independent deadlines, preserving assertions and process cleanup. Align both outer quality-check deadlines.
4. Capture safe Node/stdio failure evidence and fix demonstrated causes only.
5. Run full audit, push repair branch and follow GitHub checks. Report any remaining failures; coordinator owns main integration.

## Evidence

- Email `0d4c7b15fdee03d6ec21cfa9236484647aa42c47` matches the existing Legacy provider digest.
- All eight plugin digests at `d1a7481537c301c213a0542577991d97c8155fc5` match `resources/default-plugins.json`.
- No local eLearning commit matches the old Legacy digest. Historical Core `73f2371b` documents `cfcd286`; its clean 55-file tree has digest `f982c82b230952f6a5bdf6cf491df48aa55a96af4e776178630b0c3f85518fb4`. Do not use live source directories containing ignored runtime state.
- Phase 1: fixed checkout verification PASS for all three repositories and ten source digests; 6 source/tamper/frozen-connector checks PASS; 28 plugin, bills, frozen-connector and source-validation checks PASS. Ruff/diff checks PASS.
- Broader MCP probe: 34 PASS, 19 FAIL, 2 pre-existing SKIP. A one-case diagnostic traced the local rejection to managed uv's PyPI DNS resolving to a private/special address, not a source-pin mismatch. Keep production destination checks intact; this local environment failure is separate from the original Ubuntu missing-source failures.
- UI runner and gate deadline changes are a separate pending phase; 23 runner/quality regression checks PASS with isolated state and temporary local ports.
