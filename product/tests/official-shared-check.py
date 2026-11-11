"""Official shared-profile switch acceptance with disposable data only."""

import json
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
    fixture = home / "shared-panel-fixture"
    fixture.mkdir()
    (fixture / "package.json").write_text(
        json.dumps(
            {
                "name": "alcor-shared-panel-fixture",
                "version": "1.0.0",
                "type": "module",
                "exports": {
                    ".": "./index.js",
                    "./client": "./client.js",
                    "./package.json": "./package.json",
                },
                "dsh": {
                    "bundle": {"patch": "./cordis.patch.yml"},
                    "client": {
                        "platform": "web",
                        "inject": ["slots"],
                        "external": ["react"],
                    },
                },
            }
        )
    )
    (fixture / "cordis.patch.yml").write_text(
        "- insert:\n    - id: shared-panel-fixture\n      name: alcor-shared-panel-fixture\n"
    )
    (fixture / "index.js").write_text("export function apply() {}")
    (
        fixture / "client.js"
    ).write_text("""window.__ModuleLoader__.load({id:'alcor-shared-panel-fixture',factory(require){
      const React=require('react');return {inject:['slots'],apply(ctx){
        ctx.slots.inject('sidebar.panellist',()=>ctx.slots.register({name:'sidebar.panellist',id:'shared-panel-fixture',label:()=> 'Ecosystem fixture'},()=>null));
        ctx.slots.inject('main',()=>ctx.slots.register({name:'main',key:'shared-panel-fixture'},()=>React.createElement('h1',null,'Shared ecosystem page')));
      }};}});""")
    d.native_cli(home, "fixture-install", "add", str(fixture), "--ignore-scripts")
    with d.desktop(pw, home, "shared") as (page, errors):
        switch = page.get_by_role("button", name="切换使用模式，当前 DeepSeek Harness")
        expect(switch).to_be_visible(timeout=30000)
        switch.click()
        page.get_by_role("menuitem", name="Alcor", exact=True).click()
        expect(page.get_by_role("heading", name="事务", exact=True)).to_be_visible()
        navigation = page.get_by_role("navigation", name="Alcor 秘书导航")
        expect(navigation.get_by_role("group", name="应用")).to_be_visible()
        system = navigation.get_by_role("group", name="系统")
        expect(
            system.get_by_role("button", name="Ecosystem fixture", exact=True)
        ).to_be_visible()
        expect(
            navigation.get_by_role("group", name="应用").get_by_role(
                "button", name="Ecosystem fixture", exact=True
            )
        ).to_have_count(0)
        page.screenshot(path=str(d.ARTIFACTS / "shared.png"))
        (d.ARTIFACTS / "shared-ax.txt").write_text(page.locator("body").aria_snapshot())
        system.get_by_role("button", name="Ecosystem fixture", exact=True).click()
        expect(
            page.get_by_role("heading", name="Shared ecosystem page", exact=True)
        ).to_be_visible()
        system.get_by_role("button", name="插件", exact=True).click()
        expect(page.get_by_role("heading", name="插件", exact=True)).to_be_visible()
        alcor_bundles = d.rpc(page, "pluginManager/listBundles")
        page.get_by_role("button", name="切换使用模式，当前 Alcor").click()
        page.get_by_role("menuitem", name="DeepSeek Harness", exact=True).click()
        expect(
            page.get_by_role("button", name="切换使用模式，当前 DeepSeek Harness")
        ).to_be_visible()
        page.get_by_role("button", name="Ecosystem fixture", exact=True).click()
        expect(
            page.get_by_role("heading", name="Shared ecosystem page", exact=True)
        ).to_be_visible()
        assert d.rpc(page, "pluginManager/listBundles") == alcor_bundles
        assert not errors, errors
    with d.desktop(pw, home, "shared-restart") as (page, errors):
        expect(
            page.get_by_role("button", name="切换使用模式，当前 DeepSeek Harness")
        ).to_be_visible(timeout=30000)
        page.get_by_role("button", name="插件", exact=True).click()
        expect(page.get_by_role("heading", name="插件", exact=True)).to_be_visible()
        assert not errors, errors
    d.select_bundle(home, False)
    with d.desktop(pw, home, "disabled") as (page, errors):
        expect(page.get_by_role("button", name="插件", exact=True)).to_be_visible()
        expect(
            page.get_by_role("button", name="切换使用模式，当前 DeepSeek Harness")
        ).to_have_count(0)
        assert not errors, errors
print("PASS: official shared-profile switch, restart and disabled recovery")
