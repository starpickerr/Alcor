"""Official shared-profile switch acceptance with disposable data only."""

from pathlib import Path
import official_desktop_check as d
from fresh_profile_fixture import fresh_test_home
from playwright.sync_api import sync_playwright, expect

d.ARTIFACTS.mkdir(parents=True, exist_ok=True)
d.verify_release()
with fresh_test_home("alcor-official-shared-") as temp, sync_playwright() as pw:
    home = Path(temp)
    d.environment(home)
    with d.desktop(pw, home, "baseline") as (page, errors):
        pass
    d.native_cli(home, "install", "add", str(d.PACK), "--ignore-scripts")
    with d.desktop(pw, home, "shared") as (page, errors):
        switch = page.get_by_role("button", name="切换使用模式，当前 DeepSeek Harness")
        expect(switch).to_be_visible(timeout=30000)
        switch.click()
        page.get_by_role("menuitem", name="Alcor", exact=True).click()
        expect(page.get_by_role("heading", name="事务", exact=True)).to_be_visible()
        page.screenshot(path=str(d.ARTIFACTS / "shared.png"))
        page.get_by_role("button", name="切换使用模式，当前 Alcor").click()
        page.get_by_role("menuitem", name="DeepSeek Harness", exact=True).click()
        expect(
            page.get_by_role("button", name="切换使用模式，当前 DeepSeek Harness")
        ).to_be_visible()
        assert not errors, errors
print("PASS: unmodified official Desktop shared-profile switch")
