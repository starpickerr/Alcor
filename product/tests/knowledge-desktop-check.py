"""Exercise the packed library in an isolated official macOS Desktop with a synthetic model."""

import json
import os
import re
import runpy
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import expect, sync_playwright

import official_desktop_check as desktop
from web_check import open_secretary

configure = runpy.run_path(str(Path(__file__).with_name("shared-business-chat-check.py")))["configure"]


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        tools = [item["function"]["name"] for item in body.get("tools", [])]
        last = body["messages"][-1]
        delta, stop = {"content": "Synthetic title"}, "stop"
        if "alcor_knowledge" in tools:
            if last["role"] == "tool":
                result = json.loads(last["content"])
                assert any(row["title"] == "期末通知.png" for row in result["items"]), result
                delta = {"content": "已找到考试范围原图。"}
            else:
                delta = {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "library-search",
                            "type": "function",
                            "function": {
                                "name": "alcor_knowledge",
                                "arguments": json.dumps({"action": "search", "query": "Chapter five"}),
                            },
                        }
                    ]
                }
                stop = "tool_calls"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for data, finish in [({"role": "assistant"}, None), (delta, None), ({}, stop)]:
            event = {
                "id": "library",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "synthetic",
                "choices": [{"index": 0, "delta": data, "finish_reason": finish}],
            }
            self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")


def check(page, docs):
    open_secretary(page)
    page.get_by_role("group", name="应用", exact=True).get_by_role("button", name="知识库").click()
    expect(page.get_by_role("heading", name="还没有收藏资料")).to_be_visible()
    page.get_by_role("button", name="收藏资料", exact=True).click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("标题", exact=True).fill("教学大纲")
    dialog.get_by_label("资料集", exact=True).fill("课程")
    dialog.get_by_label("来源网址").fill("https://example.org/syllabus")
    dialog.get_by_label("文字摘录").fill("第八章为选读")
    dialog.get_by_role("button", name="保存", exact=True).click()
    page.get_by_label("筛选资料集").select_option("课程")
    page.get_by_role("button", name="网页 教学大纲").click()
    expect(dialog.get_by_role("link", name="打开原网页")).to_have_attribute(
        "href", "https://example.org/syllabus"
    )
    dialog.get_by_role("button", name="归档", exact=True).click()
    page.get_by_label("收藏范围").select_option("archived")
    page.get_by_role("button", name="网页 教学大纲").click()
    dialog.get_by_role("button", name="恢复收藏").click()
    page.get_by_label("收藏范围").select_option("active")
    page.get_by_label("筛选资料集").select_option("")
    image = Image.new("RGB", (900, 120), "white")
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 48)
    ImageDraw.Draw(image).text((20, 20), "Chapter five is optional", font=font, fill="black")
    original = docs / "期末通知.png"
    image.save(original)
    page.locator('input[type=file][accept*=".pdf"]').set_input_files(original)
    dialog.get_by_role("button", name="保存", exact=True).click()
    page.get_by_label("搜索知识库").fill("Chapter five")
    page.get_by_role("button", name="图片 期末通知.png").click(timeout=60000)
    expect(dialog.get_by_role("img", name="期末通知.png")).to_be_visible()
    with page.expect_download() as download:
        dialog.get_by_role("link", name="下载原件").click()
    assert Path(download.value.path()).read_bytes() == original.read_bytes()
    dialog.get_by_role("button", name="关闭", exact=True).click()
    # Two scanned pages exercise page numbering, preview, and the exact download.
    pdf = docs / "课程讲义.pdf"
    second = Image.new("RGB", (900, 120), "white")
    ImageDraw.Draw(second).text((20, 20), "Binary trees: CS204X", font=font, fill="black")
    image.save(pdf, "PDF", save_all=True, append_images=[second])
    page.locator('input[type=file][accept*=".pdf"]').set_input_files(pdf)
    dialog.get_by_role("button", name="保存", exact=True).click()
    expect(dialog).not_to_be_visible(timeout=60000)
    page.get_by_label("搜索知识库").fill("CS204X")
    page.get_by_role("button", name="PDF 课程讲义.pdf").click(timeout=60000)
    expect(dialog).to_contain_text("第 2 / 2 页")
    expect(dialog.locator("mark")).to_contain_text("CS204X")
    expect(dialog.locator("object")).to_have_attribute("data", re.compile("#page=2$"))
    dialog.get_by_role("button", name="关闭", exact=True).click()
    page.get_by_label("搜索知识库").fill("教学大纲")
    page.get_by_role("button", name="网页 教学大纲").click()
    dialog.get_by_role("button", name="归档", exact=True).click()
    page.get_by_label("收藏范围").select_option("archived")
    page.get_by_role("button", name="网页 教学大纲").click()
    dialog.get_by_role("button", name="永久删除", exact=True).click()
    dialog.get_by_role("button", name="取消", exact=True).click()
    expect(dialog).to_contain_text("第八章为选读")
    dialog.get_by_role("button", name="永久删除", exact=True).click()
    dialog.get_by_role("button", name="永久删除", exact=True).click()
    expect(page.get_by_role("heading", name="没有找到匹配资料")).to_be_visible()
    page.get_by_label("收藏范围").select_option("active")
    long_text = docs / "学期记录.txt"
    marker = b"LASTSECTION392"
    long_text.write_bytes(b" " * (5 * 1024 * 1024 - len(marker)) + marker)
    page.locator('input[type=file][accept*=".pdf"]').set_input_files(long_text)
    dialog.get_by_role("button", name="保存", exact=True).click()
    expect(dialog).not_to_be_visible(timeout=60000)
    page.get_by_label("搜索知识库").fill(marker.decode())
    page.get_by_role("button", name="文档 学期记录.txt").click()
    expect(dialog.locator("mark")).to_contain_text(marker.decode())
    with page.expect_download() as downloaded:
        dialog.get_by_role("link", name="下载原件").click()
    assert Path(downloaded.value.path()).read_bytes() == long_text.read_bytes()
    dialog.get_by_role("button", name="关闭", exact=True).click()
    page.set_viewport_size({"width": 540, "height": 740})
    expect(page.get_by_label("搜索知识库")).to_be_in_viewport()
    expect(page.get_by_role("button", name="收藏资料", exact=True)).to_be_in_viewport()
    page.set_viewport_size({"width": 1280, "height": 850})
    page.locator(".alcor-sidebar-chat").click()
    composer = page.locator('[data-composer-input][contenteditable="true"]')
    composer.fill("从知识库给我找出考试范围的原图。")
    composer.press("Enter")
    expect(page.get_by_text("已找到考试范围原图。", exact=True)).to_be_visible(timeout=60000)
    citation = page.get_by_role("button", name="期末通知.png", exact=True)
    citation.click()
    expect(dialog.get_by_role("img", name="期末通知.png")).to_be_visible()


if __name__ == "__main__":
    desktop.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    desktop.verify_release()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with (
            tempfile.TemporaryDirectory(prefix="alcor-library-desktop-") as temp,
            sync_playwright() as playwright,
        ):
            home = Path(temp)
            docs = configure(home, desktop.environment(home), server.server_port)
            pack = Path(os.environ.get("STELLA_SECRETARY_REVIEW_PACK", desktop.PACK))
            desktop.native_cli(home, "knowledge-install", "add", str(pack), "--ignore-scripts")
            with desktop.desktop(playwright, home, "knowledge") as (page, errors):
                check(page, docs)
                assert not errors, errors
            print(
                "PASS: collection, archive, restore, image OCR, original download, narrow layout, chat citations"
            )
    finally:
        server.shutdown()
        server.server_close()
