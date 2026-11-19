# 插件版本与 DSH 兼容发布

四个已交付插件各自维护版本：`@alcor/secretary`、`@alcor/apple-sync`、`@alcor/elearning`、`@alcor/mail`。AgentHub 插件与当前源码已移除；本机源码、安装缓存、配置和残留数据已按用户授权清理。

Tag 使用 `<插件名>-v<插件版本>`，例如 `secretary-v0.1.134`。Release 标题使用 `Alcor 0.1.134 · 适配 DSH 0.2.2-rc.5`，其他插件同样注明自己的版本和 DSH 版本，不随 DSH 同步编号。每个 Release 附该插件归档及 SHA-256，并说明验证平台、范围和未验证项。宿主为候选版时，将相关 Release 标为预发布。

DSH 更新后，先核对官方签名、版本与 app.asar 身份，再运行源码和归档检查、旧 Profile 升级、重启、业务数据及配置保留，以及插件被拦截时原生 DSH 可用检查。实际通过后才扩展精确 `engines.dsh` 与 `desktop-release.lock.json`，不得设置不受限范围或允许版本豁免。

复用 `product/tests/host-compatibility.test.mjs` 和 `product/tests/upgrade-compatibility-check.py`；用 `ALCOR_CURRENT_ARTIFACTS` 指向只包含本轮四个新归档的目录，`ALCOR_PREVIOUS_ARTIFACTS` 指向四个旧归档，其余程序与输出环境变量见产品说明。

仅因 DSH 更新而无需改包时，补充兼容验证记录；发生接口适配或精确支持范围变更时，各受影响插件独立递增补丁版本并发布。不同平台的实际验证不能互相替代。
