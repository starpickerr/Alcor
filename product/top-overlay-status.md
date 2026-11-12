# Top mode switch status

The current shared client uses one top-left Alcor/DSH switch in a reserved 44px header, matching the independent Alcor.app header. The official shell.overlay slot owns the header; host DOM markers reserve content clearance without changing official application files or replacing the layout module. Native leading controls and the narrow sidebar drawer retain their own row. All Alcor business and plugin pages share this header.

The original Alcor sidebar is restored through reversible Loader ownership, retaining native workspace/settings occupants. DSH restores its original sidebar. Alcor owns its retained automation management view and shares the native scheduler with the unchanged DSH schedules panel; no second scheduler or official panel replacement is installed. Shared settings obtain the actual mode independently of workspace partitioning and show the common workspace catalog.

Official Mac rc.2 disposable acceptance at 969144e8 passes the left header, menu keyboard opening, shared workspace identity, native automation entry, Alcor settings, 720x600 drawer opening/closing, modal blocking, restart and disabled recovery. Evidence is in the local checks/left-shared-settings-rc2 directory. The earlier centered-overlay screenshots document the superseded design.

Personal Mac business pages and four existing workspaces were observed loading after repairing an offline continuation error: one added workspace record had been omitted from the registry order. The repair appended only that existing ID, preserving records, session ownership and prior order. A credential-free native Host reproduction verified failed workspace startup/Alcor HTTP 404 before repair and active workspace/HTTP 200 afterwards. Personal switching was also confirmed manually by the user; do not label that confirmation as automated input coverage.

Native physical clicking/dragging through the Mac desktop automation currently returns noWindowsAvailable. Physical title dragging remains NOT RUN, rather than a pass inferred from CSS app-region values. Prior Windows hardware acceptance covers the earlier overlay runtime; the new reserved-header and settings commits require Windows follow-up. Keep final platform acceptance separate from green CI and from the Mac personal installation. Private continuation receipts and personal screenshots are not repository artifacts.

## Earlier review at 50c26e67 (automation superseded below)

| Review scope | Status and evidence |
| --- | --- |
| External 1–5: official window, header, switch, branding and groups | Updated and tested in official Mac rc.2 disposable Profile; personal rc.1 shows the left header and original Alcor sidebar. User confirmed switching manually. Official app identity remains DeepSeek Harness; the switch uses official primitives rather than the isolated Electron radio menu. |
| External 6/8/9; page 7: shared workspaces, plugins and settings | Common catalog and settings rendering fixed; four personal workspaces and the loaded official plugin list observed. Disposable settings test passes. Workspace assignment controls are intentionally absent from a shared catalog. |
| External 7/10/12/13: native DSH, drawer, keyboard and lifecycle | Disposable tests pass switching, narrow drawer, modal blocking, restart and disabled recovery. New Windows header/settings acceptance and Mac physical dragging remain pending; prior Windows results cover an earlier head. |
| External 11: rightbar and split dragging | Uses the official layout. Physical split dragging and narrow dual-sidebar behavior were not verified. |
| Page 1–5: tasks, calendar, briefing and bills | Business component source retained; personal pages loaded after workspace-index repair. No real edit/delete/import was performed for acceptance. |
| Page 6: automation | Both entrances open the official native schedules page; observed existing task and native create/status/search controls. Old Alcor templates, scope label and delete adapter are not restored. This follows the explicit shared-entry requirement; do not describe the old UI as equivalent or fully restored. |
| Page 8/9: plugin and host settings | Shared mode detection fixed and all five affected client bundles rebuilt. Personal Apple settings loaded successfully; broader mail/school/host-settings visual comparison remains untested. |
| Page 10: AgentHub | Shared-mode developer-session fallback fixed and unit tested; final client installed. Real developer-session creation was not executed. |

The old automation wrapper requires `schedule.manager.view` / `schedule.manager.header` supplied by the isolated compatibility module. The current shared path does not require those factories or replace the official automation module. Restoring Alcor-only controls would require a separately reviewed supported integration; it is not part of the current native entrance.

The plugin uses SDK slots, primitives and Loader lifecycle, but its native module name, panel/slot IDs and header CSS selectors (`data-shell-*`, sidebar drawer structure and chrome variable) remain host-version coupling. DOM attributes are not a guaranteed stable public API. Adapt and revalidate when upstream changes them; never patch or freeze the official installation to preserve this integration. Current success proves only the tested versions.

Latest source CI at 50c26e67: Legacy runtime quality and Native Alcor product both succeeded. Targeted final helper/mode/settings tests: 34 passed. The local integrated run had one Swift input-modified build failure (530 passed, one skipped); the isolated Apple contract rerun passed all 16. Do not report that full local run as green.


## Retained automation follow-up

Alcor now registers its own `alcor.automation.view` factory from the existing MIT-derived manager, verified against its recorded provenance. The original module's `apply` is not exported/applied; only the Alcor factory and Alcor locale are registered. Style tags and class hashes are namespaced to avoid affecting the native page. The native DSH `schedules` route remains owned by DSH.

The page defaults to all native tasks, offers Alcor-only viewing, and restores daily-briefing templates and manual creation. New Alcor-page tasks use the existing Organizer create route with Alcor ownership and preset, independent of the selected display range. Catalog/edit/run/history continue to use the one native scheduler. Existing deletion refusal remains; the public view/history omit pending delivery state, and a native confirmation dialog cannot supply an atomic safe-delete contract.

The first official rc.2 attempt exposed the removed primitives `useAnchoredPosition` export. The retained UI now uses Alcor's own CSS-anchor hook following the official migration guide, rather than modifying or backfilling official primitives. Original factory absence is not a blocker to restoring an owned view; UI compatibility must be exercised in a real official renderer.

Official Mac rc.2 final disposable acceptance passes template content, manual creation, Alcor preset ownership, all/Alcor filtering, visibility of the same task IDs in native DSH, unchanged native controls, hot disable/re-enable, narrow sidebar, modal blocking, restart and disabled recovery. Log: `/tmp/alcor-retained-automation-ui-final.log`; screenshots: local `checks/retained-automation-rc2-final`. Full product tests pass: 534 passed, one platform skip, zero failures (`/tmp/alcor-retained-automation-product-delivery-retry.log`); no test was removed or disabled. The first simultaneous repeat exited 137 before producing test output; the complete serial retry is the reported result. Secretary 0.1.103 repackages the accepted runtime with a distinct version; verify archive/client hashes at installation.
