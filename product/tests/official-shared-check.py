"""Official shared-profile switch acceptance with disposable data only."""

import json
from pathlib import Path

import official_desktop_check as d
from fresh_profile_fixture import fresh_test_home
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeout,
)
from playwright.sync_api import (
    expect,
    sync_playwright,
)

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
        expect(
            page.get_by_role("treeitem", name="默认工作区", exact=True)
        ).to_be_visible(timeout=30000)
        directory = home / "shared-workspace"
        directory.mkdir()
        workspace_request = {"request": {"path": str(directory)}}
        workspace = d.rpc(page, "workspace/create", workspace_request)["workspace"]
        switch = page.get_by_role("button", name="切换使用模式，当前 DeepSeek Harness")
        expect(switch).to_be_visible(timeout=30000)
        box = switch.bounding_box()
        assert 0 <= box["y"] < 60, box
        assert switch.evaluate("e=>getComputedStyle(e).webkitAppRegion") == "no-drag"
        switch.focus()
        switch.press("ArrowDown")
        expect(page.get_by_role("menuitem", name="Alcor", exact=True)).to_be_visible()
        page.keyboard.press("Escape")
        page.screenshot(path=str(d.ARTIFACTS / "dsh-top.png"), scale="css")
        switch.click()
        page.get_by_role("menuitem", name="Alcor", exact=True).click()
        expect(page.get_by_role("heading", name="事务", exact=True)).to_be_visible()
        navigation = page.get_by_label("Alcor 秘书导航", exact=True)
        expect(navigation.get_by_role("group", name="应用")).to_be_visible()
        expect(
            navigation.get_by_role("treeitem", name="默认工作区", exact=True)
        ).to_be_visible()
        expect(
            navigation.get_by_role("treeitem", name="shared-workspace", exact=True)
        ).to_be_visible()
        existing = d.rpc(page, "workspace/create", workspace_request)
        assert (
            not existing["created"]
            and existing["workspace"]["workspaceId"] == workspace["workspaceId"]
        )
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
        page.get_by_role("button", name="清单管理", exact=True).click()
        expect(page.get_by_role("dialog", name="清单管理", exact=True)).to_be_visible()
        try:
            page.get_by_role("button", name="切换使用模式，当前 Alcor").click(
                trial=True, timeout=1000
            )
        except PlaywrightTimeout:
            pass  # Native modal must block interaction with the background switch.
        else:
            raise AssertionError("Mode switch intercepted the native modal")
        page.keyboard.press("Escape")
        page.set_viewport_size({"width": 720, "height": 600})
        narrow_switch = page.get_by_role("button", name="切换使用模式，当前 Alcor")
        expect(narrow_switch).to_be_visible()
        bounds = narrow_switch.bounding_box()
        assert (
            bounds["x"] >= 0
            and bounds["x"] + bounds["width"] <= 720
            and bounds["y"] < 60
        ), bounds
        opener = page.locator('[data-shell-leading]').get_by_role(
            'button', name='展开侧边栏', exact=True
        )
        expect(opener).to_be_visible()
        opener.click()
        expect(navigation).to_have_attribute('role', 'dialog')
        expect(navigation).to_have_attribute('aria-modal', 'true')
        expect(navigation.get_by_role('group', name='应用')).to_be_visible()
        navigation.get_by_role('button', name='收起侧边栏', exact=True).click()
        expect(opener).to_be_visible()
        page.screenshot(path=str(d.ARTIFACTS / "alcor-top-narrow.png"), scale="css")
        page.set_viewport_size({"width": 1280, "height": 900})
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
        expect(
            page.get_by_role("treeitem", name="shared-workspace", exact=True)
        ).to_be_visible()
        assert not errors, errors
    with d.desktop(pw, home, "shared-save-alcor") as (page, errors):
        page.get_by_role("button", name="切换使用模式，当前 DeepSeek Harness").click()
        page.get_by_role("menuitem", name="Alcor", exact=True).click()
        expect(page.get_by_label("Alcor 秘书导航", exact=True)).to_be_visible()
        expect(
            page.get_by_role("treeitem", name="默认工作区", exact=True)
        ).to_be_visible()
        assert not errors, errors
    with d.desktop(pw, home, "shared-restart") as (page, errors):
        expect(
            page.get_by_role("button", name="切换使用模式，当前 Alcor")
        ).to_be_visible(timeout=30000)
        expect(page.get_by_label("Alcor 秘书导航", exact=True)).to_be_visible()
        expect(
            page.get_by_role("treeitem", name="默认工作区", exact=True)
        ).to_be_visible()
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
