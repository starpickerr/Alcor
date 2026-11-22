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
                if "edges" in result:
                    names = {r["id"]: r["title"] for r in result["nodes"]}
                    assert any(names[e["source"]] == "复习提纲" and names[e["target"]] == "期末通知.png" for e in result["edges"]), result
                    delta = {"content": "复习提纲引用了期末通知。"}
                else:
                    assert any(row["title"] == "期末通知.png" for row in result["items"]), result
                    delta = {"content": "已找到考试范围原图。"}
            else:
                action = {"action": "graph"} if "查图谱" in str(last["content"]) else {"action": "search", "query": "Chapter five"}
                delta = {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "library-search",
                            "type": "function",
                            "function": {
                                "name": "alcor_knowledge",
                                "arguments": json.dumps(action),
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
    add = page.get_by_role("button", name="添加资料", exact=True)
    expect(page.get_by_role("button", name=re.compile("^(导入文件|收藏资料)$"))).to_have_count(0)
    add.press("Enter")
    expect(page.get_by_role("menuitem", name="导入文件", exact=True)).to_be_visible()
    page.get_by_role("menuitem", name="收藏资料", exact=True).click()
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
    add.click()
    with page.expect_file_chooser() as chooser:
        page.get_by_role("menuitem", name="导入文件", exact=True).click()
    chooser.value.set_files(original)
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
    for title, body in [
        ("复习计划", "先阅读 [[复习提纲]] 和 [[教学大纲]]，再完成 [[自测题]]。"),
        ("课程笔记", "整理 [[课程讲义.pdf]] 的例题和 [[概念清单]]。"),
        ("例题集", "参考 [[课程笔记]] 和 [[自测题]]。"),
        ("概念清单", "重点来自 [[教学大纲]]。"),
        ("自测题", "结合 [[课程讲义.pdf]] 与 [[概念清单]]。"),
    ]:
        desktop.rpc(page, "alcorOrganizer/business", {"request": {"domain": "knowledge", "method": "save", "arguments": {"title": title, "text": body}}})
    add.click()
    page.get_by_role("menuitem", name="收藏资料", exact=True).click()
    dialog.get_by_label("标题", exact=True).fill("复习提纲")
    dialog.get_by_label("笔记", exact=True).fill("参考 [[教学大纲]] 与 ")
    dialog.get_by_role("button", name="插入资料引用").click()
    dialog.get_by_label("查找要引用的资料").fill("期末通知")
    dialog.locator(".alcor-reference-picker").get_by_role("button", name=re.compile("期末通知")).click()
    dialog.get_by_role("button", name="保存", exact=True).click()
    page.get_by_role("button", name="图谱", exact=True).click()
    network = page.get_by_role("region", name="知识图谱")
    node = network.get_by_role("button", name="打开资料：复习提纲", exact=True)
    expect(node).to_be_visible()
    expect(network.locator("line")).to_have_count(12)
    transform = network.locator("svg > g")
    initial = transform.get_attribute("transform")
    network.get_by_role("button", name="放大图谱").click()
    expect(transform).not_to_have_attribute("transform", initial)
    network.get_by_role("button", name="适应窗口").click()
    expect(transform).to_have_attribute("transform", initial)
    network.screenshot(path=str(desktop.ARTIFACTS / "graph-desktop.png"))
    page.emulate_media(color_scheme="dark")
    page.wait_for_function('document.body.hasAttribute("data-ds-dark-theme")')
    network.screenshot(path=str(desktop.ARTIFACTS / "graph-dark.png"), animations="disabled")
    page.emulate_media(color_scheme="light")
    page.wait_for_function('!document.body.hasAttribute("data-ds-dark-theme")')
    node.focus()
    node.press("Enter")
    expect(dialog.get_by_role("region", name="引用了")).to_contain_text("期末通知.png")
    dialog.locator(".alcor-knowledge-text").get_by_role("button", name="教学大纲").click()
    expect(dialog.get_by_role("region", name="被引用")).to_contain_text("复习提纲")
    dialog.get_by_role("button", name="返回上一份资料").click()
    expect(dialog.get_by_role("region", name="引用了")).to_be_visible()
    dialog.get_by_role("button", name="关闭", exact=True).click()
    page.set_viewport_size({"width": 540, "height": 740})
    expect(network.get_by_role("button", name="适应窗口")).to_be_in_viewport()
    page.mouse.move(500, 70)
    page.keyboard.press("Escape")
    expect(page.locator("[data-sidebar-drawer]")).to_have_attribute("data-sidebar-collapsed", "true")
    page.wait_for_function("""() => {
        const el = document.querySelector('.alcor-library-graph'), box = el.getBoundingClientRect()
        return el.contains(document.elementFromPoint(box.left + 15, box.top + 15))
    }""")
    network.screenshot(path=str(desktop.ARTIFACTS / "graph-narrow.png"), animations="disabled")
    page.get_by_label("筛选图谱标题").fill("复习提纲")
    expect(network.locator("line")).to_have_count(4)
    expect(network.get_by_role("button", name="打开资料：期末通知.png")).to_be_visible()
    page.get_by_label("筛选图谱标题").fill("不存在的资料")
    expect(page.get_by_text("当前范围没有资料")).to_be_visible()
    page.set_viewport_size({"width": 1280, "height": 850})
    page.get_by_role("button", name="文档", exact=True).click()
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
    expect(add).to_be_in_viewport()
    page.set_viewport_size({"width": 1280, "height": 850})
    page.locator(".alcor-sidebar-chat").click()
    composer = page.locator('[data-composer-input][contenteditable="true"]')
    composer.fill("从知识库给我找出考试范围的原图。")
    composer.press("Enter")
    expect(page.get_by_text("已找到考试范围原图。", exact=True)).to_be_visible(timeout=60000)
    citation = page.get_by_role("button", name="期末通知.png", exact=True)
    citation.click()
    expect(dialog.get_by_role("img", name="期末通知.png")).to_be_visible()
    dialog.get_by_role("button", name="关闭", exact=True).click()
    composer.fill("查图谱，复习提纲引用了哪张通知？")
    composer.press("Enter")
    expect(page.get_by_text("复习提纲引用了期末通知。", exact=True)).to_be_visible(timeout=60000)


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
                "PASS: collection, archive, restore, image OCR, original download, graph navigation, backlinks, narrow layout, chat search and graph tools"
            )
    finally:
        server.shutdown()
        server.server_close()
