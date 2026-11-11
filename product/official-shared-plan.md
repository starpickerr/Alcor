# Official shared-profile implementation

Baseline: 02217cd4. Preserved remote: archive/isolated-runtimes-20261112-02217cd4.
Branch: feat/official-dsh-shared-20261112.

Use the official application's external plugin profile and public main/sidebar/shell.bottom slots. Reuse the mode controller; preserve existing sessions, drafts and business services. Stop replacing official layout/workspace/settings/schedule modules. Keep official update/signature paths intact. Shared mode is a presentation choice, not process isolation.

Validate mode restoration and plugin teardown, package configuration, native tests/build, and official macOS Desktop in disposable data before personal installation. Do not migrate personal databases or remove the old isolated environment. Coordinate Windows changes before integration; main is out of scope.

Status: implementation checkpoint; integration and personal installation pending.

Evidence: 524 product tests pass, including 24 mode tests; native package builds. Official macOS 0.2.2-rc.1 signature verification passes. Disposable official Desktop installs the package and both switch directions pass; the existing Desktop harness then fails during process-group cleanup with PermissionError, so the complete acceptance run is FAIL, not PASS. No test-owned matching process remained in the subsequent process inventory.

Personal installation is intentionally pending: the official desktop Profile currently has no @alcor/secretary dependency or alcor/business directory, whereas current business data lives in the retained isolated environment. Installing into that Profile without an agreed data strategy would display empty business data. Do not automatically migrate or overwrite it.

Windows should reuse the secretary package/slots; this checkpoint does not edit environments.mjs, client-shell, or public platform startup code. The optional isolated design remains preserved, not discarded.
