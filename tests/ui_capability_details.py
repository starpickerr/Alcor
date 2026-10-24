"""Unified directory/detail interactions and legacy configuration entry points."""

import json
import os
import re
from urllib.parse import parse_qs, urlparse
from playwright.sync_api import expect, sync_playwright

URL = os.environ.get("STELLA_TEST_URL", "http://127.0.0.1:5173")
ROWS = [
    dict(
        kind=k, id=i, name=n, description=d, enabled=True, state=s, source=o, version=v
    )
    for k, i, n, d, s, o, v in [
        (
            "plugins",
            "fixture.plugin",
            "日历插件",
            "管理日历与事件",
            "stopped",
            "installed",
            "1.2.0",
        ),
        ("mcp", "Documents", "Documents MCP", "查询文档", "ready", "stdio", None),
        ("skills", "review", "Code Review", "代码审查规范", "enabled", "custom", None),
        ("tools", "read", "读取文件", "读取授权文件", "available", "builtin", None),
    ]
]
posts = []
failed = [False]
pending = []
hold = [False]
hold_write = [False]
pending_write = []
partial_error = [False]
malformed_plugin = [False]
missing_plugin = [False]
english_only = [False]
settings_requests = []
preferences = {"revision": 1, "values": {"note": "before"}, "secretFields": ["token"]}
preference_saves = []
conflict = [False]


def backend(route):
    path = urlparse(route.request.url).path
    query = parse_qs(urlparse(route.request.url).query)
    data = {"status": "success"}
    if path == "/auth/check":
        data.update(auth_required=False, authenticated=True)
    elif path == "/api/health":
        data["status"] = "ok"
    elif path == "/api/models":
        data.update(
            providers=[{"id": "test", "configured": True, "label": "Test"}],
            capabilities={
                "chat": {"current_provider": "test", "current_model": "test"}
            },
        )
    elif path == "/api/capabilities":
        if "id" in query:
            row = next((r for r in ROWS if r["id"] == query["id"][0]), None)
            if row is None:
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(
                        {"status": "error", "message": "Capability not found"}
                    ),
                    headers={
                        "Access-Control-Allow-Origin": URL,
                        "Access-Control-Allow-Credentials": "true",
                    },
                )
                return
            tabs = (
                ["overview"]
                if row["kind"] == "tools"
                else ["overview", "content"]
                if row["kind"] == "skills"
                else ["overview", "features", "runtime"]
            )
            if row["kind"] == "plugins":
                tabs = ["overview", "features", "configuration", "runtime"]
            data["detail"] = {
                **row,
                "tabs": tabs,
                "overview": {"en": "Published MCP documentation"} if english_only[0] else "# 真实说明\n\n安全的 **Markdown** 介绍。",
                "content": "# 审查步骤\n检查正确性。",
                "features": [
                    {
                        "id": "search",
                        "name": "搜索文档",
                        "description": "查找已连接的文档",
                    }
                ],
                "runtime": {"state": row["state"], "generation": 0},
            }
            if hold[0]:
                pending.append((route, data))
                return
        else:
            data.update(
                items=ROWS, errors={"mcp": "unavailable"} if partial_error[0] else {}
            )
    elif path == "/api/plugins/runtime":
        if not malformed_plugin[0]:
            data["plugins"] = [
                {
                    "pluginId": "other.plugin" if missing_plugin[0] else "fixture.plugin",
                    "name": "日历插件",
                    "intent": "enabled",
                    "state": "stopped",
                    "version": "1.2.0",
                }
            ]
    elif path == "/api/plugins/settings":
        body = route.request.post_data_json
        settings_requests.append(body)
        if body["action"] == "describe":
            data["result"] = [
                {
                    "id": "fixture.settings",
                    "categories": [{"id": "general", "title": "偏好"}],
                    "fields": [
                        {
                            "key": "note",
                            "title": "备注",
                            "category": "general",
                            "type": "string",
                        },
                        {
                            "key": "token",
                            "title": "密钥",
                            "category": "general",
                            "type": "string",
                            "secret": True,
                        },
                    ],
                }
            ]
        elif body["action"] == "read":
            data["result"] = preferences
        elif body["action"] == "save":
            preference_saves.append(body)
            if conflict[0]:
                data = {"status": "error", "message": "Configuration changed"}
            else:
                assert body["expectedRevision"] == preferences["revision"]
                assert body["changes"] == {"note": "draft"}
                preferences["values"].update(body["changes"])
                preferences["revision"] += 1
                data["result"] = preferences
    elif (
        path in {"/api/tools", "/api/mcp", "/api/skills", "/api/plugins/manage"}
        and route.request.method == "POST"
    ):
        body = route.request.post_data_json
        posts.append(body)
        if failed[0]:
            data = {"status": "error", "message": "保存失败，请重试"}
        else:
            row = next(
                r
                for r in ROWS
                if r["id"]
                == (body.get("id") or body.get("name") or body.get("pluginId"))
            )
            if body.get("action") != "install":
                row["enabled"] = body.get(
                    "enabled", body.get("action") in {"enable", "open"}
                )
                row["state"] = "disabled" if not row["enabled"] else "available"
            data.update(server={}, result={})
            if hold_write[0]:
                pending_write.append((route, data))
                return
    route.fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(data),
        headers={
            "Access-Control-Allow-Origin": URL,
            "Access-Control-Allow-Credentials": "true",
        },
    )


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 920})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script(
            "localStorage.setItem('stella_lang','zh'); window.electronAPI={platform:'darwin',getBackendPort:async()=>9876,onBackendStatus:()=>()=>{},getBackendError:async()=>null};"
        )
        page.route("http://127.0.0.1:9876/**", backend)
        page.goto(URL + "/#/skills", wait_until="networkidle")
        expect(page.get_by_role("tab")).to_have_text(
            ["全部", "插件", "MCP", "Skill", "内置工具"]
        )
        expect(page.get_by_role("link", name="读取文件", exact=True)).to_be_visible()
        page.get_by_role("button", name="搜索能力", exact=True).click()
        page.get_by_role("searchbox", name="搜索能力", exact=True).fill("代码")
        expect(page.get_by_role("link", name="Code Review", exact=True)).to_be_visible()
        expect(page.get_by_role("link", name="读取文件", exact=True)).to_have_count(0)
        page.get_by_role("searchbox", name="搜索能力", exact=True).fill("")
        page.screenshot(path="/tmp/stella-capability-directory.png", full_page=True)
        page.get_by_role("link", name="读取文件", exact=True).click()
        detail = page.locator("[data-capability-detail]")
        expect(detail.get_by_role("tab")).to_have_text(["概览"])
        expect(detail.get_by_role("heading", name="真实说明")).to_be_visible()
        expect(detail.get_by_role("button", name="设置", exact=True)).to_have_count(0)
        expect(detail.get_by_role("button", name="更多", exact=True)).to_have_count(0)
        failed[0] = True
        detail.get_by_role("button", name="停用", exact=True).click()
        expect(detail.get_by_role("alert")).to_contain_text("保存失败")
        expect(detail.get_by_role("button", name="停用", exact=True)).to_be_enabled()
        failed[0] = False
        detail.get_by_role("button", name="停用", exact=True).click()
        expect(detail.get_by_role("button", name="启用", exact=True)).to_be_visible()
        assert posts[-1] == {"id": "read", "enabled": False}
        page.get_by_role("link", name="返回原页面", exact=True).click()
        page.get_by_role("link", name="日历插件", exact=True).click()
        expect(page.get_by_role("heading", name="日历插件", exact=True)).to_be_visible()
        page.screenshot(path="/tmp/stella-capability-detail.png", full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path="/tmp/stella-capability-detail-mobile.png", full_page=True)
        page.get_by_role("button", name="收起侧栏", exact=True).click()
        page.screenshot(
            path="/tmp/stella-capability-detail-mobile-compact.png", full_page=True
        )
        page.evaluate("localStorage.setItem('cow_theme', 'dark')")
        page.reload(wait_until="networkidle")
        expect(page.locator("html")).to_have_class(
            re.compile(r"\bdark\b")
        )
        page.screenshot(
            path="/tmp/stella-capability-detail-mobile-dark.png", full_page=True
        )
        page.evaluate("localStorage.setItem('cow_theme', 'light')")
        page.reload(wait_until="networkidle")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.goto(URL + "/#/skills/skills/review", wait_until="networkidle")
        page.get_by_role("tab", name="内容", exact=True).click()
        expect(page.get_by_role("heading", name="审查步骤")).to_be_visible()
        page.goto(URL + "/#/skills/mcp/Documents", wait_until="networkidle")
        page.get_by_role("tab", name="功能", exact=True).click()
        expect(page.get_by_text("搜索文档", exact=True)).to_be_visible()
        hold[0] = True
        page.goto(
            URL + "/#/skills/plugins/fixture.plugin", wait_until="domcontentloaded"
        )
        expect(page.get_by_role("status")).to_contain_text("正在读取能力")
        assert pending
        hold[0] = False
        page.goto(URL + "/#/skills/tools/read", wait_until="networkidle")
        expect(page.get_by_role("heading", name="读取文件", exact=True)).to_be_visible()
        old_route, old_data = pending.pop()
        old_route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(old_data),
            headers={
                "Access-Control-Allow-Origin": URL,
                "Access-Control-Allow-Credentials": "true",
            },
        )
        expect(page.get_by_role("heading", name="读取文件", exact=True)).to_be_visible()
        hold_write[0] = True
        page.get_by_role("button", name="启用", exact=True).click()
        expect(page.get_by_role("button", name="处理中…", exact=True)).to_be_disabled()
        assert pending_write
        hold_write[0] = False
        page.goto(URL + "/#/skills/mcp/Documents", wait_until="networkidle")
        old_route, old_data = pending_write.pop()
        old_route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(old_data),
            headers={
                "Access-Control-Allow-Origin": URL,
                "Access-Control-Allow-Credentials": "true",
            },
        )
        expect(
            page.get_by_role("heading", name="Documents MCP", exact=True)
        ).to_be_visible()
        partial_error[0] = True
        page.goto(URL + "/#/skills", wait_until="networkidle")
        expect(page.get_by_role("alert")).to_contain_text("部分目录暂不可用")
        expect(page.get_by_role("link", name="读取文件", exact=True)).to_be_visible()
        partial_error[0] = False
        page.get_by_role("button", name="重试", exact=True).click()
        expect(page.get_by_role("alert")).to_have_count(0)
        page.get_by_role("button", name="添加能力", exact=True).click()
        expect(page.get_by_role("dialog")).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.get_by_role("dialog")).to_have_count(0)
        page.get_by_role("button", name="添加能力", exact=True).click()
        page.get_by_role("textbox", name="从插件目录安装", exact=True).fill(
            "fixture.plugin"
        )
        page.get_by_role("button", name="安装插件", exact=True).click()
        expect(page.get_by_role("heading", name="日历插件", exact=True)).to_be_visible()
        assert posts[-1] == {"action": "install", "pluginId": "fixture.plugin"}
        page.get_by_role("tab", name="配置", exact=True).click()
        note = page.get_by_role("textbox", name="备注", exact=True)
        expect(note).to_have_value("before")
        expect(page.get_by_role("textbox", name="密钥", exact=True)).to_have_count(0)
        note.fill("draft")
        page.get_by_role("tab", name="概览", exact=True).click()
        page.get_by_role("tab", name="配置", exact=True).click()
        expect(note).to_have_value("draft")
        conflict[0] = True
        page.get_by_role("button", name="保存本类别", exact=True).click()
        expect(page.get_by_role("alert")).to_contain_text("Configuration changed")
        expect(note).to_have_value("draft")
        conflict[0] = False
        preferences["revision"] = 2
        preferences["values"]["note"] = "server"
        page.get_by_role("button", name="读取最新版本并保留修改", exact=True).click()
        expect(note).to_have_value("draft")
        page.get_by_role("button", name="保存本类别", exact=True).click()
        expect(page.get_by_text("本类别已保存", exact=True)).to_be_visible()
        assert preference_saves[-1]["expectedRevision"] == 2
        assert preference_saves[-1]["pluginId"] == "fixture.plugin"
        malformed_plugin[0] = True
        page.reload(wait_until="networkidle")
        expect(page.get_by_role("alert")).to_contain_text("无法读取插件目录")
        expect(page.get_by_role("heading", name="日历插件", exact=True)).to_be_visible()
        malformed_plugin[0] = False
        missing_plugin[0] = True
        settings_requests.clear()
        page.reload(wait_until="networkidle")
        expect(page.get_by_role("alert")).to_contain_text("此插件已不可用")
        expect(page.get_by_role("textbox", name="备注", exact=True)).to_have_count(0)
        assert not settings_requests, settings_requests
        missing_plugin[0] = False
        english_only[0] = True
        mcp = next(row for row in ROWS if row['kind'] == 'mcp')
        mcp['description'] = {'zh': '  ', 'en': 'Find published documents'}
        page.goto(URL + '/#/skills?tab=mcp', wait_until='networkidle')
        expect(page.get_by_role('link', name='Documents MCP', exact=True)).to_contain_text('Find published documents')
        page.get_by_role('link', name='Documents MCP', exact=True).click()
        expect(page.get_by_text('Published MCP documentation', exact=True)).to_be_visible()
        english_only[0] = False
        page.goto(URL + "/#/skills/tools/not-found", wait_until="networkidle")
        expect(page.get_by_role("alert")).to_contain_text("Capability not found")
        page.get_by_role("link", name="返回原页面", exact=True).click()
        expect(page).to_have_url(URL + "/#/skills?tab=tools")
        assert not errors, errors
        print(
            "PASS: all types, search, Markdown, builtin overview, save recovery, stale read/write, partial inventory retry, install, configuration draft/CAS recovery, malformed configuration, navigation and mobile layout"
        )
    finally:
        browser.close()
