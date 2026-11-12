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
- Phase 3 runner: all 18 scripts retained in order; separate unbuffered child processes keep the original runpy import behavior, 90 seconds per script, start/end/duration/exit evidence, later scripts still run after failures, and owned process cleanup. Full/incremental UI gate deadlines cover the complete batch; CI job budget is 60 minutes. 23 runner/quality regression checks PASS with isolated state and temporary local ports. Actual renderer checks still pending.
- Phase 2 local fixes: declare the three document libraries already used by the existing parser tests; the synthetic download fixture authorizes only its own explicit local origin. 12 artifact and document-parser checks PASS, including digest tampering and path-escape rejection; production HTTP policy unchanged.

## Resumed scope: current plugin architecture

The user resumed CI work and explicitly requested adaptation to the current official DSH plugin product rather than blindly repairing the retired self-hosted Agent. No test deletion, assertion weakening or new skip is authorized as a means to green checks. The three earlier pending cache changes were continued and committed as `dc0487ca`; their 21-case run had completed before the pause.

- Ownership, exact trigger conditions and old-assumption/current-capability mappings: `docs/development/ci-coverage.md`.
- Default complete audit now builds/tests the current plugin; historical complete audit has an explicit `audit-legacy` target. Lazy Legacy interpreter selection keeps current Make targets independent of retired Conda discovery while preserving explicit Legacy overrides.
- Native CI gains actual Linux plugin/security checks, native isolated Keychain checks, and fixed-current-source frozen connector protocol checks. Retained isolated-shell product tests remain in the full suite.
- Legacy maintenance remains automatic for its code/dependencies/checks and on manual request. Native source-only changes no longer select the unrelated old Agent/Electron gate. Full Legacy/Keychain maintenance runs on macOS; Linux process/HTTP/SSRF checks remain separate. Manual full audit is no longer redundantly preceded by the incremental gate.
- Current baseline: 534 PASS / 0 FAIL / 1 existing Windows-only SKIP in the complete 535-case product suite. The restricted first attempt's 6 local process/socket/Swift-cache permission failures are retained in `/tmp/alcor-ci-native-baseline-20261112.log`; the normal-permission rerun passed, log `/tmp/alcor-ci-native-baseline-unrestricted-20261112.log`.
- New actual Host-bundle/Python-import boundaries plus current-Make independence: 3 PASS, log `/tmp/alcor-ci-native-boundary-final-unrestricted-20261112.log`. Legacy interpreter/quality regression: 29 PASS.
- One preserved Legacy/macOS full baseline is still running with its original assertions; log `/tmp/alcor-ci-legacy-macos-baseline-20261112.log`. Do not conflate its failures with the current plugin product.
- Workflow YAML and shell syntax PASS. Final product audit, new Keychain/frozen connector jobs and final-head GitHub checks pending.
