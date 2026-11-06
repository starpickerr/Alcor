"""Exercise the installed DSH Web, native plugin, persistence, and a synthetic provider."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
requests = []


class Provider(BaseHTTPRequestHandler):
    shared_tools = frozenset()

    def log_message(self, *_args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        requests.append(payload)
        tools = payload.get("tools", [])
        assert self.headers.get("Authorization") == "Bearer synthetic-test-key"
        if tools:
            names = [tool["function"]["name"] for tool in tools]
            if "stella_overview" in names:
                assert {"stella_overview", "stella_business", "stella_generate_briefing", "stella_review_tasks", "stella_import_bills", "stella_import_dates", "stella_export_bills"} <= set(names), names
                assert self.shared_tools <= set(names), names
            else:
                assert self.shared_tools <= set(names), names
                assert set(names) - self.shared_tools == {"todo_write", "ask_user_question"}, names
                assert "你是 Stella" not in json.dumps(payload, ensure_ascii=False)
        completed = any(message["role"] == "tool" for message in payload["messages"])
        delta = {"content": "Stella synthetic tool round trip passed." if completed else "Synthetic title"}
        if tools and "stella_overview" not in names:
            delta = {"content": "DSH synthetic conversation passed."}
        reason = "stop"
        if tools and "stella_overview" in names and not completed:
            delta = {"tool_calls": [{"index": 0, "id": "call_overview", "type": "function", "function": {"name": "stella_generate_briefing" if "生成并保存今日简报" in json.dumps(payload["messages"], ensure_ascii=False) else "stella_review_tasks" if "回顾本机未完成事务" in json.dumps(payload["messages"], ensure_ascii=False) else "stella_overview", "arguments": "{}"}}]}
            reason = "tool_calls"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for choice in [{"delta": {"role": "assistant"}, "finish_reason": None}, {"delta": delta, "finish_reason": None}, {"delta": {}, "finish_reason": reason}]:
            data = {"id": "chatcmpl-synthetic", "object": "chat.completion.chunk", "created": 1, "model": "stella-mock", "choices": [{"index": 0, **choice}]}
            self.wfile.write(("data: " + json.dumps(data) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def start(home, logfile, port=0):
    environment = {**os.environ, "STELLA_PRODUCT_HOME": str(home)}
    process = subprocess.Popen(["node", "scripts/product/web.mjs", "--no-open", "--port", str(port)], cwd=ROOT, env=environment, stdout=logfile, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        log = Path(logfile.name).read_text()
        if match := re.search(r"dsh web: (http://\S+)", log):
            if "did not activate" in log:
                stop(process)
                raise AssertionError(re.sub(r"token=\S+", "token=[redacted]", log))
            return process, match.group(1)
        if process.poll() is not None:
            raise AssertionError(log)
        threading.Event().wait(0.05)
    process.terminate()
    process.wait(timeout=10)
    raise AssertionError("Web did not become ready")


def stop(process):
    process.terminate()
    process.wait(timeout=15)


def open_page(context, url):
    page = context.new_page()
    page.set_viewport_size({"width": 1280, "height": 850})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.add_locator_handler(page.get_by_role('button', name='Continue', exact=True), lambda button: button.click())
    page.goto(url)
    page.get_by_role('button', name=re.compile('切换使用模式')).wait_for()
    wait_secretary(page)
    return page, errors


def open_secretary(page):
    mode = page.get_by_role('button', name=re.compile('切换使用模式'))
    if mode.count():
        if mode.get_attribute('aria-label').endswith('DeepSeek Harness'):
            mode.click()
            page.get_by_role('menuitem', name='Stella', exact=True).click()
        page.get_by_role('button', name='事务', exact=True).click()
    else:
        settings(page)
        page.get_by_role('button', name='打开 Stella', exact=True).click()
    page.get_by_role('heading', name='事务', exact=True).wait_for()


def wait_secretary(page):
    mode = page.get_by_role('button', name=re.compile('切换使用模式'))
    mode.or_(page.get_by_role('button', name='Account menu', exact=True)).first.wait_for()
    if page.get_by_role('button', name='Continue', exact=True).count():
        page.get_by_role('button', name='Continue', exact=True).click()
    if mode.count() and mode.get_attribute('aria-label').endswith('Stella'):
        page.locator('.stella-sidebar').wait_for()
    settings(page)
    page.get_by_label('助手名称', exact=True).wait_for()
    page.get_by_role('button', name='Close', exact=True).click()


def start_secretary_chat(page):
    open_secretary(page)
    page.locator('.stella-sidebar-chat').click()


def settings(page):
    page.get_by_role("button", name=re.compile(r"^(Account menu|账号菜单|More|更多)$")).last.click()
    page.get_by_role("menuitem", name=re.compile(r"^(Settings|设置)")).click()
    page.get_by_role("dialog", name=re.compile(r"^(Settings|设置)$")).get_by_role("button", name="Stella", exact=True).click()


def rpc(page, url, endpoint, args=None):
    response = page.request.post(url.split('/?')[0] + '/api/' + endpoint, data={
        'type': 'client-request', 'rpcId': str(uuid4()), 'method': endpoint, 'payload': {'args': args or {}}})
    assert response.ok, response.status
    result = response.json()['result']
    assert result['ok'], result
    return result.get('value')



def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory(prefix="stella-dsh-web-check-") as temporary, sync_playwright() as playwright:
        home = Path(temporary)
        (home / ".stella-product.json").write_text('{"version":1,"kind":"web"}\n')
        profile = home / "profiles/stella"
        profile.mkdir(parents=True)
        (profile / "cordis.patch.yml").write_text(f"""- id: agent-default-model
  config:
    provider: stella-test
    model: stella-mock
- id: llm-pi-ai
  config:
    providers:
      stella-test:
        api: openai-completions
        apiKeyEnv: STELLA_SYNTHETIC_KEY
        baseURL: http://127.0.0.1:{server.server_port}/v1
        models:
          - id: stella-mock
            name: Synthetic model
            contextWindow: 32000
            maxTokens: 4096
            input: [text]
""")
        # Synthetic credential only. Real credentials must never use this fixture.
        (home / ".credentials.yaml").write_text("version: 1\nrefs:\n  STELLA_SYNTHETIC_KEY: synthetic-test-key\n")
        os.chmod(home / ".credentials.yaml", 0o600)
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        with (home / "web.log").open("w") as log:
            process, url = start(home, log)
            try:
                page, errors = open_page(context, url)
                expect(page.get_by_role('button', name='事务', exact=True)).to_have_count(0)
                page.get_by_role('button', name='切换使用模式，当前 DeepSeek Harness', exact=True).click()
                page.get_by_role('menuitem', name='Stella', exact=True).click()
                page.get_by_role("button", name="事务", exact=True).click()
                page.get_by_role("heading", name="事务", exact=True).wait_for()
                assert page.request.post(url.split("/?")[0] + "/api/stellaSecretary/overview", data={}).ok
                settings(page)
                page.get_by_label("助手名称", exact=True).fill("Stella regression")
                page.get_by_role("button", name="保存", exact=True).click()
                page.get_by_text("已保存", exact=True).wait_for()
                assert rpc(page, url, 'stellaSecretary/overview')['displayName'] == 'Stella regression'
                page.get_by_role("button", name="Close", exact=True).click()
                start_secretary_chat(page)
                composer = page.locator('[data-composer-input][contenteditable="true"]')
                composer.fill("Read stella_overview and report the result.")
                composer.press("Enter")
                page.get_by_text("Stella synthetic tool round trip passed.", exact=True).wait_for(timeout=25000)
                assert any(any(message["role"] == "tool" for message in request["messages"]) for request in requests)
                page.get_by_role("button", name="事务", exact=True).click()
                page.get_by_role("heading", name="事务", exact=True).wait_for()
                composer = page.locator('[data-composer-input][contenteditable="true"]')
                # Keep the existing conversation/draft while changing the product entry.
                page.get_by_role("button", name="New session", exact=True).last.click()
                composer.fill("Unsent secretary draft")
                secretary_location = page.evaluate('localStorage.getItem("dsh.sessions.current")')
                second, second_errors = open_page(context, url)
                second.get_by_role('button', name='切换使用模式，当前 Stella', exact=True).wait_for()
                count_before = len(requests)
                page.get_by_role("button", name="切换使用模式，当前 Stella", exact=True).click()
                page.get_by_role("menuitem", name="DeepSeek Harness", exact=True).click()
                expect(page.get_by_role('button', name='事务', exact=True)).to_have_count(0)
                expect(page.locator('.stella-desk')).to_have_count(0)
                assert len(requests) == count_before
                expect(second.get_by_role('button', name='切换使用模式，当前 Stella', exact=True)).to_be_visible()
                assert second_errors == [], second_errors
                second.close()
                page.get_by_role("button", name="New session", exact=True).last.click()
                expect(composer).to_be_visible()
                assert page.evaluate('localStorage.getItem("dsh.sessions.current")') != secretary_location
                composer.fill("DSH protocol test")
                composer.press("Enter")
                page.get_by_text("DSH synthetic conversation passed.", exact=True).wait_for(timeout=25000)
                assert any(set(t["function"]["name"] for t in item.get("tools", [])) == {"todo_write", "ask_user_question"} for item in requests)
                page.get_by_role("button", name="切换使用模式，当前 DeepSeek Harness", exact=True).click()
                page.get_by_role("menuitem", name="Stella", exact=True).click()
                expect(composer).to_have_text("Unsent secretary draft")
                page.get_by_role("button", name="事务", exact=True).click()
                # Expanded and collapsed desktop rail keep the same top-level entry.
                page.locator('[data-sidebar-toggle]:visible').click()
                page.get_by_role('button', name='切换使用模式，当前 Stella', exact=True).click()
                page.get_by_role('menuitem', name='DeepSeek Harness', exact=True).click()
                page.get_by_role('button', name='切换使用模式，当前 DeepSeek Harness', exact=True).click()
                page.get_by_role('menuitem', name='Stella', exact=True).click()
                page.locator('[data-sidebar-toggle]:visible').click()
                page.set_viewport_size({"width": 390, "height": 844})
                mode_button = page.get_by_role("button", name="切换使用模式，当前 Stella", exact=True)
                # Real hit testing catches a visible button covered by the drawer.
                page.locator('[data-sidebar-opener]').click()
                expect(page.get_by_role('dialog', name='Stella 秘书导航', exact=True)).to_be_visible()
                mode_button.click()
                page.get_by_role('menuitem', name='DeepSeek Harness', exact=True).click()
                page.get_by_text('DSH synthetic conversation passed.', exact=True).wait_for()
                page.get_by_role('button', name='切换使用模式，当前 DeepSeek Harness', exact=True).click()
                page.get_by_role('menuitem', name='Stella', exact=True).click()
                mode_button.click()
                page.get_by_role('menuitem', name='Stella', exact=True).press('Escape')
                mode_button.focus()
                mode_button.press("ArrowDown")
                page.get_by_role("menuitem", name="Stella", exact=True).press("ArrowDown")
                page.get_by_role("menuitem", name="DeepSeek Harness", exact=True).press("Escape")
                assert mode_button.evaluate("e => e === document.activeElement")
                if directory := os.environ.get("STELLA_CHECK_ARTIFACTS"):
                    Path(directory).mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(Path(directory) / "dual-mode-mobile.png"))
                    page.set_viewport_size({"width": 1280, "height": 850})
                    page.screenshot(path=str(Path(directory) / "dual-mode-desktop.png"))
                assert page.locator("body").evaluate("e => e.scrollWidth <= e.clientWidth")
                assert errors == [], errors
                page.get_by_role('button', name='切换使用模式，当前 Stella', exact=True).click()
                page.get_by_role('menuitem', name='DeepSeek Harness', exact=True).click()
                restart_port = int(re.search(r':(\d+)/', url).group(1))
                page.close()
            except Exception:
                if directory := os.environ.get("STELLA_CHECK_ARTIFACTS"):
                    Path(directory).mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(Path(directory) / "web-failure.png"))
                print("Browser errors:", errors)
                print('Overview:', rpc(page, url, 'stellaSecretary/overview'))
                print('Navigation:', page.evaluate('localStorage.getItem("stella.product-mode.v1")'))
                print("Mode controls:", page.locator('.stella-mode-bar').inner_text() if page.locator('.stella-mode-bar').count() else 'missing')
                raise
            finally:
                stop(process)
        with (home / "restart.log").open("w") as log:
            process, url = start(home, log, restart_port)
            try:
                page, errors = open_page(context, url)
                page.get_by_role('button', name='切换使用模式，当前 DeepSeek Harness', exact=True).wait_for()
                page.get_by_text('DSH synthetic conversation passed.', exact=True).wait_for()
                page.get_by_role('button', name='切换使用模式，当前 DeepSeek Harness', exact=True).click()
                page.get_by_role('menuitem', name='Stella', exact=True).click()
                page.get_by_role("button", name="事务", exact=True).click()
                page.get_by_role("heading", name="事务", exact=True).wait_for()
                assert errors == [], errors
                page.close()
            except Exception:
                if directory := os.environ.get("STELLA_CHECK_ARTIFACTS"):
                    Path(directory).mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(Path(directory) / "web-failure.png"))
                print("Browser errors:", errors)
                print("Mode controls:", page.locator('.stella-mode-bar').inner_text() if page.locator('.stella-mode-bar').count() else 'missing')
                raise
            finally:
                stop(process)
        # Product launchers must respect owner disablement on the next boot.
        with (home / "disable.log").open("w") as log:
            process, url = start(home, log)
            try:
                page, errors = open_page(context, url)
                result = rpc(page, url, 'pluginManager/setBundleEnabled', {'name': '@stella/secretary', 'enabled': False})
                assert result['application'] == 'applied', result
                expect(page.get_by_role('button', name='事务', exact=True)).to_have_count(0)
                expect(page.locator('.stella-mode-bar')).to_have_count(0)
                page.close()
            finally:
                stop(process)
        with (home / "disabled-restart.log").open("w") as log:
            process, url = start(home, log)
            try:
                page = context.new_page()
                page.goto(url)
                page.get_by_role('button', name='Account menu', exact=True).wait_for()
                assert page.get_by_role('button', name='事务', exact=True).count() == 0
                assert not any(preset['id'] == 'stella' for preset in rpc(page, url, 'agentPresets/list')['presets'])
                page.close()
            finally:
                stop(process)
        context.close()
        browser.close()
    server.shutdown()
    print("PASS: dual-mode Web, scoped tool catalogs, draft return, drawer hit testing, keyboard, settings restart; synthetic provider only")


if __name__ == "__main__":
    main()
