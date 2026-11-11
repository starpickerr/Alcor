# Official shared-profile implementation

Baseline: 02217cd4. Preserved remote: archive/isolated-runtimes-20261112-02217cd4.
Branch: feat/official-dsh-shared-20261112.

Use the official application's external plugin profile and public main/sidebar/shell.bottom slots. Reuse the mode controller; preserve existing sessions, drafts and business services. Stop replacing official layout/workspace/settings/schedule modules. Keep official update/signature paths intact. Shared mode is a presentation choice, not process isolation.

Validate mode restoration and plugin teardown, package configuration, native tests/build, and official macOS Desktop in disposable data before personal installation. Do not migrate personal databases or remove the old isolated environment. Coordinate Windows changes before integration; main is out of scope.

Status: implementation checkpoint; integration and personal installation pending.

Evidence: 524 product tests pass, including 24 mode tests; native package builds. Official macOS 0.2.2-rc.1 signature verification passes. Disposable official Desktop installs the package and both switch directions pass; the existing Desktop harness then fails during process-group cleanup with PermissionError, so the complete acceptance run is FAIL, not PASS. No test-owned matching process remained in the subsequent process inventory.

Personal installation is intentionally pending: the official desktop Profile currently has no @alcor/secretary dependency or alcor/business directory, whereas current business data lives in the retained isolated environment. Installing into that Profile without an agreed data strategy would display empty business data. Do not automatically migrate or overwrite it.

Windows should reuse the secretary package/slots; this checkpoint does not edit environments.mjs, client-shell, or public platform startup code. The optional isolated design remains preserved, not discarded.

## Follow-up validation and integration

The original process-group PermissionError did not recur in two full switch runs. A separate exited-child process-group probe returned ESRCH normally. A diagnostic wrapper retained PermissionError and captured PID/PGID/UID/state if it recurred; it did not. Do not claim a proven root cause or suppress permission failures. Expanded official macOS acceptance (switch, restart, cold bundle disable and native recovery) completed successfully with no security changes.

The shared implementation no longer starts the legacy per-space navigation/projection service. DSH restores its native conversation panel instead of the custom dsh workbench panel. 25 mode tests pass. Windows shared foundation 57c4f60 is integrated; isolated prototype 32bb8dd is not. Integrated focused run: 34 pass, 1 platform skip, 0 failures.

Delivery gap under the latest request: current official sidebar preserves official workspace and plugin rendering, but does not reproduce the original Alcor application/system grouping. The original SecretarySidebar requires workspace.browser and settings.shell factories, absent in the inspected official 0.2.2-rc.1 client. Official workspace registers sidebar.workspaces; its parent is the official sidebar. Do not copy private render internals or claim the current sidebar meets unchanged UI requirements. Continue resolving composition before develop integration.

## Personal data continuation proposal (not executed)

Read-only inventory found six business SQLite databases in the retained isolated Profile: agent-hub, bills, briefing, calendar, projection and tasks. The target official desktop Profile has no alcor/business directory. Organizer currently derives this path from profileContext.dir.

A continuation must preserve whole schemas, primary keys, foreign-key references, revision/operation journals and pending delivery state, not recreate task rows or copy only visible entries. Also inventory corresponding storage domains, account bindings, Apple sync mappings, source credentials and referenced session/workspace IDs before copying. Shared DSH sessions and credentials already exist at the target; do not overwrite or blindly merge them.

Recommended sequence for coordination: confirm source ownership, enumerate exact related files and reference remapping, stop both writers, retain original paths, snapshot only touched files/SQLite through a consistent offline copy, stage into an unselected target, validate integrity/reference resolution and counts, activate one shared owner, keep the isolated environment stopped and reversible. Do not use live symlinks as a shortcut or allow simultaneous sync/QQ responders. No personal migration is authorized by this proposal.

Git 2.55.0 and gh 2.101.0 are installed; gh authenticated successfully and normal fetch/push work. No merge CLI error was observed; no develop merge has been attempted at this checkpoint.

Cleanup inventory: shared-worktree node_modules 584 MB, package artifact 1.2 MB, four task-owned screenshot/log directories total approximately 2.1 MB. Keep source worktrees, archive branches, original personal environments and recovery copies. Do not delete these candidates until acceptance evidence and user cleanup authorization are resolved.
