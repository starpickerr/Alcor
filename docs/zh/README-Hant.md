<p align="center"><img src="../../packages/alcor-secretary/src/assets/alcor-avatar.svg" alt="Alcor" width="112" /></p>

# Alcor

[English](../../README.md) · [中文](README.md) · [繁體中文](README-Hant.md) · [日本語](../ja/README.md)

Alcor 是在 DeepSeek Harness（DSH）中執行的個人助理。透過原生外掛，你可以管理工作與行事曆、記錄帳單、產生簡報及設定自動化。Alcor 與 DSH 共用桌面視窗、模型、對話及工作區。

在 Alcor 頁面上，將滑鼠移到小熊上或點選它即可對話。小熊跟隨目前的工作區，切換頁面可以繼續聊。拖曳小熊可以調整位置，在 **設定 → Alcor** 中可以調整懸停行為、顯示小熊或恢復預設位置。

## 桌面安裝

目前的桌面目標為 Apple Silicon Mac。請使用[相容版本清單](../../product/desktop-release.lock.json)中列出的官方 DSH 用戶端。

1. 在已初始化的 DSH Profile 中開啟外掛管理員，從 `.tgz` 檔案安裝 `@alcor/secretary`。
2. 按需安裝下表中的選用外掛。
3. 結束並重新開啟 DSH，在視窗頂端的模式選單中選擇 **Alcor**。

舊 Stella Profile 需先完成遷移，再安裝更名後的外掛。安裝及打包步驟見[原生產品指南](../../product/README.md)。

| 外掛 | 功能 |
| --- | --- |
| `@alcor/secretary` | 管理工作和子工作、檢視行程、記錄帳單、產生簡報及安排定時更新。 |
| `@alcor/apple-sync` | 選擇來源並授權後，與 Apple 行事曆和提醒事項同步。 |
| `@alcor/elearning` | 連接復旦 eLearning 帳號，讀取課程、作業和公告。 |
| `@alcor/mail` | 連接 IMAP 信箱，以唯讀方式查詢郵件並提供簡報來源。 |
| `@alcor/agent-hub` | 在 DSH 工作台追蹤開發工作與 Agent 執行記錄。 |

## 本機開發

需要 Node.js 24.9 以上及 Python 3.9 以上。建置 macOS 輔助程式還需 Xcode Command Line Tools。請在儲存庫根目錄執行下列指令。

```sh
npm ci --ignore-scripts
npm run product:build
npm test
npm run dev
```

開發預覽使用獨立的 DSH Profile。執行 `npm run product:pack` 可在 `.local/artifacts/` 產生可安裝的秘書外掛。建置學校及郵件外掛還需要相鄰的 MCP 儲存庫及指定版本的建置依賴，詳見 [MCP 接入指南](../../product/mcp-connections.md)。

原生外掛位於 `packages/`，建置腳本位於 `scripts/product/`，整合測試位於 `product/tests/`。早期獨立 Python/Electron 應用程式的說明見[舊版指南](guide-Hant.md)。

## 專案來源與授權

Alcor 基於 zhayujie 及貢獻者開發的 [CowAgent](https://github.com/zhayujie/CowAgent)（原 `chatgpt-on-wechat`）獨立開發，沿用上游的 Agent、模型、工具、技能、記憶和訊息通道基礎，並增加桌面工作流程，以及事務、日程、帳單、簡報和安全管理等共用業務服務。

來源與版權說明見[第三方聲明](../../THIRD_PARTY_NOTICES.md)，授權條款見 [MIT 授權](../../LICENSE)。

## 作者的話

這是我第一次獨立完成的開源專案，感謝 Codex 和 Cursor 在開發過程中提供的幫助。專案仍有需要完善的地方，歡迎透過[問題回報](https://github.com/starpickerr/Alcor/issues)提交建議和缺陷報告。
