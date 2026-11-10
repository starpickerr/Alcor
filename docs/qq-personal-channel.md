# QQ personal-account channel

The channel uses an external QQ client with NapCat and the native `dsh-onebot-qq` bundle. DSH owns the Agent, Alcor persona, durable sessions, and schedules. QQ login is completed by the user in the local client.

## Pinned integration

| Component | Version |
| --- | --- |
| Official DSH Desktop | 0.2.2-rc.1 |
| Alcor secretary | Keep the current installed native package |
| App Store QQ for Mac | 7.0.3 (54395) |
| NapCat | 4.18.42 |
| NapCat Mac Installer | 1.8 |
| dsh-onebot-qq | 0.9.19-alcor.3, derived from npm 0.9.19 |
| dsh-lan-memory | 2.11.1-alcor.1, derived from Git commit `4edabd09b2f4258a902abb754bfb2d455db3e908` |

The upstream bundle is [mario841859784/dsh-onebot](https://github.com/mario841859784/dsh-onebot), licensed BSD-3-Clause. Preserve its `LICENSE` in derived packages. NapCat is separately installed and has its own restricted license.

The [startup mapping patch](../product/patches/dsh-onebot-0.9.19-startup-mapping.patch) applies to the unpacked npm package. After waiting for persisted mappings, `createChat` checks whether startup has already restored that chat. This prevents an early reconnect message from replacing a resumed session. The first derived package contains this fix. Apply the [group-channel patch](../product/patches/dsh-onebot-qq-group-chat.patch) to that version. The [group-memory patch](../product/patches/dsh-lan-memory-group-chat.patch) applies to the installed 2.11.1 tree, including its existing local changes.

## Runtime configuration

- Install with the official Desktop plugin manager or its bundled CLI while Desktop is fully stopped. Keep `autoInstallPeers: false`; never install replacement `@deepseek-ai/*` packages in the Profile.
- NapCat exposes one authenticated OneBot WebSocket server on `127.0.0.1:3001`. The DSH plugin uses forward mode and the same locally generated secret. Bind the NapCat WebUI to loopback as well.
- Set an explicit QQ workspace and media directory. Disable both private and group inbound messages until the owner is configured. Then allowlist the owner for private messages and administrator commands. Enable explicitly selected groups with `agentPresetGroup: qq-group-chat`. Set `requireMention: false` for natural participation or `true` for mentions only.
- Bind private QQ sessions to `alcor` and group sessions to `qq-group-chat`. Alcor's private business tools retain their existing source authorization; selecting a persona does not grant a QQ sender access to local business data, credentials, or private MCP sources.
- Disable intermediate message streaming, debug injection, and inbound recording. Preserve self-message filtering and send-result checks.
- A native Schedule must target an existing mapped QQ session. Its final plain-text output is routed to that QQ chat. A new generic Alcor session has no QQ recipient mapping.
- Keep the Profile manifest, lockfile, overrides, and current Alcor package pins recoverable before installation. Credentials, QQ identifiers, and personal configuration stay outside Git.

Official-Host tests use synthetic OneBot peers and a synthetic model. They verify channel routing and session contracts; actual QQ login, delivery, model access, and long-running operation require separate acceptance.

The personal-account deployment separately verified QQ login, one outgoing test message to the configured owner, and a real incoming message with an Alcor reply confirmed by the user. Scheduled delivery and restart recovery were tested with a synthetic model and OneBot. Both QQ/NapCat and DSH must remain running; this setup does not install an automatic login item. The official Mac loader selects NapCat with `--no-sandbox`, which requires the user's explicit security decision.

## Group conversations and permissions

The group-channel patch gives each group an actual `qq-group-chat` composition, a separate session namespace, and a workspace under `<workspacePath>/groups/<groupId>`. Resumed mappings must match that group and workspace. Group slash commands cannot switch presets, sessions, models, or workspaces. Group inbound processing accepts text; quote expansion, forwarded messages, media downloads, and automatic steering are disabled for groups.

Every member of an allowed group can mention Alcor and use that group's shared memory. The existing **Administrator users** setting (`adminUsers`) is also the local-tool whitelist. Only members in that list can use the native file, search, terminal, working-directory and job tools. Existing DSH execution permissions still apply. Replies and tool results are delivered to the group, so administrator requests must account for that audience. Private Alcor business tools, global LAN memory tools, other chats, configuration tools, and delegation are not added to the group catalog.

The bridge stamps the real OneBot sender and group into message-source metadata. At each step the runtime validates every claimed input; a non-whitelisted or unknown input prevents local-tool use for the entire turn. A later administrator message cannot upgrade that turn. The guard checks again at execution, including the current administrator list. Body text, nicknames, role labels, remembered statements, and `pre-execute: allow` do not grant authority. An unbound group preset, scheduler input, unknown source, or restarted Agent has no local-tool permission.

## Shared group memory

Install both derived packages together. `dsh-lan-memory` retains its separate personal store and excludes `onebot-group-*` sessions from personal capture. Each group instead reuses the same capture, storage, search, and compilation engine with a store under `<mediaDir>/group-memory/<groupId>`; private legacy-store migration is disabled there. Do not move personal memory into this directory.

Conversations that enter the Agent are captured locally, preserve speaker attribution, and survive restart. Members can explicitly ask to remember or recall group facts. Recent records become available immediately; background consolidation runs in batches of six pending records or after the configured idle interval (30 minutes by default). A failed compilation leaves pending records intact. Prompt context and recall results have fixed byte budgets. Group memory is shared with the group and must not contain credentials or unrelated private material.

The paired patches preserve upstream BSD-3-Clause and MIT licenses. Synthetic official-Host tests cover allowed and denied execution, message routing, restart, workspace and memory isolation; real group delivery must be confirmed separately after installation. This integration does not provide OS isolation from installed native plugin code.

## Natural participation

The [participation patch](../product/patches/dsh-onebot-qq-group-participation.patch) builds on 0.9.19-alcor.2. With **Require mention** turned off, an actual @ or a message addressed to Alcor by name goes directly to the group Agent. For ordinary text in an allowed group, a short model call considers up to eight recent lines from that group and chooses whether to join. It batches bursts, spaces decisions at least ten seconds apart, ignores messages explicitly addressed to another member, and stays silent if the decision fails. Recent text stays in a two-minute memory buffer. When the Agent joins, recent context can enter the saved group conversation.

The decision has no tools. A turn that draws on nearby members’ conversation can use group memory. An administrator can explicitly @ Alcor or begin a message with its name when requesting a local tool.
