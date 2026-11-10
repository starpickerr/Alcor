# DeepSeek Harness 来源

仓库 https://github.com/deepseek-ai/deepseek-harness ，发行 tag `dsh-v0.2.2-alpha.3`，提交 `05b1cae8db6e674c5685101494d4eec24652424a`。研究的官方 master 为 `8bd073392eef6f54e921a093b95076248eb08cbb`，不作为运行输入。

Copyright (c) 2026 DeepSeek. MIT 完整许可保留在 `DSH-LICENSE.txt`。

- `product/patches/0001-desktop-private-environment.patch` 改写 `apps/desktop/src/main.ts`。
- `product/patches/0002-client-product-entry.patch` 改写 `packages/client/ui-layout/src/client/{index.ts,AppFrame.tsx,AppFrame.module.css}` 与 `packages/client/ui-workspace/src/client/navigation.ts`。
- Stella Bundle 的 `vendor/` 保存五个原生 UI 模块的 MIT 衍生产物，不替换官方 App 文件；来源和摘要见 `NATIVE-UI-PROVENANCE.json`。未建立独立 UI/会话框架。
- Secretary 的 `src/remote.js` 引用官方 `@deepseek-ai/dsh-typert-protocol/remote-info`；`scripts/product/build-client.mjs` 在 Client 产物内打包该 helper，随原生 bundle 保留 `DSH-LICENSE.txt`。
- `product/patches/0003-patched-transitive-dependencies.patch` 调整官方 `pnpm-workspace.yaml` 和 `pnpm-lock.yaml`，与根 npm overrides 同步四项已知漏洞修复版本；保留原配置的其他安全规则。
- 其余 npm 依赖各自保留包内许可证；DSH 的 vendored Cordis 保留其 MIT（Copyright 2021-present Shigma）许可。

已有 Codex Apache-2.0 适配、许可与 NOTICE 未修改。旧 Runtime 的来源记录仍见 `docs/architecture/runtime-reference-integration.md`；该文描述旧 Python Runtime，不能当作本 DSH 原型已实现安全边界的证明。

`0004-workspace-browser-factory.patch` 与 `0005-settings-shell-factory.patch` 分别使原生工作区和原生账号／设置 Shell 可被产品侧栏复用；原组件和其许可归属不变。

`0006-schedule-manager-factory.patch` 将原生自动化列表、完整编辑器和运行记录通过 `schedule.manager.view` 复用，并增加模板草稿及来源显示；调度仍由 DSH Host 提供。

`scripts/product/build-native-ui.mjs` 对固定版本的 `ui-settings-models` Client 仅修改首次使用提示的设置命名空间；其 Host 适配器沿用原 Profile 的 `credentialOnboarding` 配置。原始 Host 随包保留，变换前后摘要与适配器完整性均在构建时验证。
