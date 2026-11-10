<p align="center"><img src="packages/alcor-secretary/src/assets/alcor-avatar.svg" alt="Alcor" width="112" /></p>

# Alcor

[English](README.md) · [中文](docs/zh/README.md) · [繁體中文](docs/zh/README-Hant.md) · [日本語](docs/ja/README.md)

Alcor is a personal assistant for DeepSeek Harness (DSH). Its native plugins add task lists, a calendar, bills, briefings and automation to the desktop client. You can switch between Alcor and DSH in the same window and keep using the host’s models, conversations and workspaces.

On Alcor pages, hover over or click the bear to chat in your current workspace. Drag it to move it. Its settings are under **Settings → Alcor**.

## Desktop installation

The supported desktop target is macOS on Apple Silicon. Use an official DSH release listed in the [compatibility manifest](product/desktop-release.lock.json).

1. Open the plugin manager in an initialized DSH profile and install the `@alcor/secretary` bundle from its `.tgz` file.
2. Install any optional plugins you need from the table below.
3. Quit and reopen DSH, then select **Alcor** in the mode menu at the top of the window.

Existing Stella profiles need to be migrated before installing the renamed bundles. See the [native product guide](product/README.md) for installation and packaging details.

| Plugin | What it adds |
| --- | --- |
| `@alcor/secretary` | Manage tasks and subtasks, view events, record bills, generate briefings and schedule updates. |
| `@alcor/apple-sync` | Sync events and tasks with Apple Calendar and Reminders after selecting and authorizing sources. |
| `@alcor/elearning` | Connect a Fudan eLearning account to read courses, assignments and announcements. |
| `@alcor/mail` | Connect IMAP mailboxes for read-only queries and briefing sources. |
| `@alcor/agent-hub` | Track development tasks and agent execution in the DSH workbench. |

## Local development

Use Node.js 24.9 or later and Python 3.9 or later. Building the macOS helpers also requires the Xcode Command Line Tools. From the repository root:

```sh
npm ci --ignore-scripts
npm run product:build
npm test
npm run dev
```

The development preview uses an isolated DSH profile. To build the secretary plugin for desktop installation, run `npm run product:pack`. The tarball is written to `.local/artifacts/`. The school and mail bundles also require the adjacent MCP repositories and their pinned build dependencies. See the [connector guide](product/mcp-connections.md).

Native plugin sources live under `packages/`, build scripts under `scripts/product/`, and integration tests under `product/tests/`. The earlier standalone Python/Electron application is documented in the [legacy guide](docs/guide.md).

## Source and license

Alcor is independently developed from [CowAgent](https://github.com/zhayujie/CowAgent) (formerly `chatgpt-on-wechat`) by zhayujie and contributors. It reuses the upstream Agent, model, tool, skill, memory, and messaging foundations, and adds desktop workflows plus shared business services for tasks, calendars, bills, briefings, and security management.

See [third-party notices](THIRD_PARTY_NOTICES.md) for source attribution and the [MIT license](LICENSE) for license terms.

## A note from the author

This is the first open-source project I completed on my own. Thanks to Codex and Cursor for help during development. There is still work to do; suggestions and bug reports through [Issues](https://github.com/starpickerr/Alcor/issues) are welcome.
