# QQ personal-account channel

The channel uses an external QQ client with NapCat and the native `dsh-onebot-qq` bundle. DSH owns the Agent, Alcor persona, durable sessions, and schedules. QQ login is completed by the user in the local client.

## Pinned integration

| Component | Version |
| --- | --- |
| Official DSH Desktop | 0.2.2-rc.1 |
| Alcor secretary | 0.1.66 |
| App Store QQ for Mac | 7.0.3 (54395) |
| NapCat | 4.18.42 |
| NapCat Mac Installer | 1.8 |
| dsh-onebot-qq | 0.9.19-alcor.1, derived from npm 0.9.19 |

The upstream bundle is [mario841859784/dsh-onebot](https://github.com/mario841859784/dsh-onebot), licensed BSD-3-Clause. Preserve its `LICENSE` in derived packages. NapCat is separately installed and has its own restricted license.

The [startup mapping patch](../product/patches/dsh-onebot-0.9.19-startup-mapping.patch) applies to the unpacked npm package. After waiting for persisted mappings, `createChat` checks whether startup has already restored that chat. This prevents an early reconnect message from replacing a resumed session. The derived package changes only this fix and its package version.

## Runtime configuration

- Install with the official Desktop plugin manager or its bundled CLI while Desktop is fully stopped. Keep `autoInstallPeers: false`; never install replacement `@deepseek-ai/*` packages in the Profile.
- NapCat exposes one authenticated OneBot WebSocket server on `127.0.0.1:3001`. The DSH plugin uses forward mode and the same locally generated secret. Bind the NapCat WebUI to loopback as well.
- Set an explicit QQ workspace and media directory. Disable both private and group inbound messages until the owner is configured. Then allowlist the owner for private messages and administrator commands. Keep group messaging disabled until its permission scope has been reviewed.
- Bind QQ sessions to the `alcor` persona. Alcor's private business tools retain their existing source authorization; selecting a persona does not grant a QQ sender access to local business data, credentials, or private MCP sources.
- Disable intermediate message streaming, debug injection, and inbound recording. Preserve self-message filtering and send-result checks.
- A native Schedule must target an existing mapped QQ session. Its final plain-text output is routed to that QQ chat. A new generic Alcor session has no QQ recipient mapping.
- Keep the Profile manifest, lockfile, overrides, and current Alcor package pins recoverable before installation. Credentials, QQ identifiers, and personal configuration stay outside Git.

Official-Host tests use synthetic OneBot peers and a synthetic model. They verify channel routing and session contracts; actual QQ login, delivery, model access, and long-running operation require separate acceptance.

The personal-account deployment separately verified QQ login, one outgoing test message to the configured owner, and a real incoming message with an Alcor reply confirmed by the user. Scheduled delivery and restart recovery were tested with a synthetic model and OneBot. Both QQ/NapCat and DSH must remain running; this setup does not install an automatic login item. The official Mac loader selects NapCat with `--no-sandbox`, which requires the user's explicit security decision.
