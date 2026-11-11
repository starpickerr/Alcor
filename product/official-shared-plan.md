# Official shared-profile implementation

Current checkpoint: original Alcor sidebar restored, official DSH sidebar restored on switch-back, native workspace/settings shared. rc.1 and rc.2 renderer acceptance includes visible workspace trees and Alcor-mode restart. The rc.2 empty-tree defect was a missing public sectionScroller inject/hookContext contract and is fixed. Earlier footer-prototype gaps below are historical, superseded by the restored sidebar implementation. Personal continuation remains separate and is not yet complete.

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

## Public-slot grouping prototype

The sidebar.footer.action public list now hosts Alcor-owned application/system groups with existing app icons. System entries derive their IDs from the live official sidebar.panellist registry; they select the original main key rather than register duplicate plugin pages. The original official workspace and plugin manager remain untouched. The shared-navigation test explicitly keeps AuraBot out of application items and preserves its original route ID.

Official macOS grouped acceptance passes: real group accessibility nodes, a standard ecosystem fixture under System only, native plugin page, same ecosystem main heading from both modes, identical pluginManager/listBundles result, restart, and disabled recovery. The fixture is not the user's real AuraBot and sends no messages. Screenshot /tmp/alcor-official-grouped/shared.png and AX /tmp/alcor-official-grouped/shared-ax.txt expose the remaining visual difference.

Public-slot constraint (installed 0.2.2-rc.1 ui-sidebar/lib/client.js): PanelRow lines 212-235 wraps sidebar.panellist contributions in a button and aria-hidden glyph; arbitrary grouped navigation there would create nested controls/inaccessible content. Lines 507-540 flatten panel metadata into one nav with no group field. The native sidebar owns sidebar.workspaces and sidebar.settings at lines 733-771; the custom component cannot claim or render these children while that owner remains. The available arbitrary-content footer list is rendered after the workspace at lines 541-565. Hence this prototype puts groups below the official workspace and leaves official global rows above it. It does NOT yet meet identical original navigation design. Do not add private DOM portals, copied render implementations or hashed-class layout patches to disguise that difference.

Minimum upstream seam for unchanged positioning: a public sidebar navigation-content seat before sidebar.workspaces (with wide/collapse information), plus a supported way for its occupant to group or replace the global panel presentation without taking ownership of workspace/settings. Alternatively officially expose reusable workspace/settings factories. Pending coordination, keep this prototype reviewable; no develop integration.

## Finite personal continuation scope

Source: retained isolated desktop Profile's alcor/business/{agent-hub,bills,briefing,calendar,projection,tasks}.sqlite3; destination: corresponding official desktop Profile alcor/business files, currently absent. Preserve the full file contents and transaction journals via offline SQLite backup, including operation/revision receipts and cross-database projection state; never reconstruct only visible tasks.

Related candidates requiring a coordinated choice before activation: Profile alcor/apple-sync/{apple-calendar,apple-reminders}.sqlite3 for external identity mappings; home storages/alcor_organizer.json and alcor_secretary.json (source exists, target absent). Do NOT copy alcor_spaces.json as shared mode no longer uses separate workspace ownership. Do NOT overwrite target sessions/workspaces or copy entire HOME/Profile, node_modules, browser storage or pnpm cache.

The source business schema references external_session_ref/workspace_ref in external_runs, source/account IDs and sync cursors in briefing, source keys and tombstones in calendar/tasks, and operation IDs in bills/tasks. Validate those references against target native catalogs before importing dependent history; unresolved session/workspace links must remain archived or receive explicit per-ID mapping, never silently point at a new default workspace.

Credential continuation is separate: migration-contract.js names alcor-elearning/account and alcor-email/accounts; compare describeRecord metadata and existing target account IDs without reading secret payloads. Preserve existing target credentials. Apple external identity mappings must be consistent with copied business IDs before enabling sync; do not run source and target writers concurrently. The existing legacy migration marker describes Stella-to-Alcor, not isolated-to-shared relocation, so do not forge or reuse it as proof that this continuation completed. No files have been transferred.
