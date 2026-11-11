# Top mode switch status

The current shared implementation places one bidirectional mode button in the official shell.overlay list, positioned at the top center. It does not modify the official archive, signature, native window controls or layout module. DSH mode does not mount Alcor application/system navigation.

Mac 0.2.2-rc.1 checks: normal and 720x600 viewports, menu keyboard opening, live switch in both directions, modal blocking, restart and disabled recovery pass. Button app-region is no-drag. Physical native title dragging is NOT RUN because macOS was locked; the test continuation marker does not count as a successful drag. Windows chrome/caption overlap is pending hardware validation.

This is not a dedicated reserved toolbar seat. Other plugin overlays or main-page content may share its coordinates. Current screenshots do not prove all pages, all widths or future host versions collision-free. The original Alcor sidebar is now restored through reversible Loader ownership, with native workspace/settings occupants. DSH restores its original sidebar. The footer grouping prototype has been removed. rc.2 scroller injection and Alcor-mode restart are explicitly covered by acceptance.

Screenshots from the matching top-switch source are in /Users/starpickerr/.local/share/Alcor/checks/top-overlay-preview-d1b642c1/{dsh-mode,alcor-mode,modal,dsh-narrow,alcor-narrow}.png. They contain only disposable synthetic data. No personal data was migrated.
