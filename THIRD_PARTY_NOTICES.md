# Source attribution

Stella is independently developed from [CowAgent](https://github.com/zhayujie/CowAgent), formerly `chatgpt-on-wechat`, created by zhayujie and contributors. The inherited code includes the Agent runtime, model adapters, tool and skill systems, memory and knowledge foundations, and messaging integrations.

Stella adds its desktop client and business workflows, including tasks, calendars, bills, briefings, application services, and security management. Upstream capabilities continue to inform the project; contributors retain their original authorship.

The repository [LICENSE](LICENSE) preserves the MIT terms and both copyright notices:

- Copyright (c) 2022 zhayujie
- Copyright (c) 2026 starpickerr

Third-party Python and npm packages retain their respective licenses. Their versions are recorded in the dependency manifests and lockfile; the repository's MIT license does not replace their license terms.

## OpenAI Codex

`core/security/process_environment.py` adapts the platform Core environment lists,
case-insensitive selection and Windows PATHEXT fallback from
[`codex-rs/protocol/src/shell_environment.rs`](https://github.com/openai/codex/blob/ac558e2a3264e1025fd8025fdd71d03dfbe8c86e/codex-rs/protocol/src/shell_environment.rs),
on official `main` at `ac558e2a3264e1025fd8025fdd71d03dfbe8c86e`.
The Rust policy implementation was reduced to a fixed Python allowlist for Stella;
configurable inheritance and overrides were not ported.

Copyright 2025 OpenAI. This adapted file is licensed under Apache-2.0;
see [license](licenses/codex/LICENSE) and the retained upstream [NOTICE](licenses/codex/NOTICE).
The repository MIT license does not replace these terms. No Ratatui code was copied.

## DeepSeek Harness design reference

The plugin instance ownership and stale-load publication rules were informed by
[`vendor/cordis/src/{registry,fiber,utils}.ts`](https://github.com/deepseek-ai/deepseek-harness/tree/8bd073392eef6f54e921a093b95076248eb08cbb/vendor/cordis/src),
official `master` at `8bd073392eef6f54e921a093b95076248eb08cbb`.
Stella retains its Python RPC runtime; no Harness/Cordis source or dependency was copied.
Harness uses MIT (Copyright 2026 DeepSeek); its vendored Cordis license is MIT
(Copyright 2021-present Shigma). Source comparisons and adaptation decisions are
recorded in [runtime reference integration](docs/architecture/runtime-reference-integration.md).
