# @stella/privacy

可选的 macOS Keychain 凭据 provider，支持 DSH `0.2.2-alpha.3` 的 reference、API Key record 和 OAuth grant record 契约。安装默认停用，不改变用户已有的凭据、模型、执行策略或其他插件。

## 启用与恢复

通过原生管理器安装后，先安全停止受影响的宿主，备份 Profile 配置，再由用户明确修改自己的 Profile：

```yaml
- id: credentials
  disabled: true
- id: stella-keychain
  disabled: false
```

`credentials` 是当前受支持 DSH 版本的原生文件 provider。必须同时停用它，避免同一作用域重复提供服务。Stella 安装流程不写入这些启用配置。可在 `stella-keychain.config.keychainPath` 中指定已有专用 Keychain，否则使用 macOS 默认 Keychain；不会创建或解锁系统密钥库，也不弹出认证窗口。

新 provider 不自动导入 `.credentials.yaml`、`.env` 或旧 Stella Keychain；启用前应在受控测试 Profile 中重新配置所需凭据。启用后，Models 等原生消费者保存的新凭据进入 Keychain，JSON/YAML 只保留原有引用。原文件不会被删除或改写，回退到文件 provider 会重新使用旧文件值，不能视为最新凭据的恢复；需要先核对轮换和撤销状态。

停用或卸载前先选择可用的凭据 provider，重启宿主。加密条目默认保留，可在相同规范化 `DSH_HOME` 及同一 Keychain 上重新启用；移动数据根需要独立迁移，复制目录不能恢复密钥。

## 存储与失败

每个规范化 `DSH_HOME` 使用独立 Keychain service。reference 与 record 作为一个版本化 JSON 文档加密保存，整个文档上限 1 MiB；磁盘只使用官方跨进程锁记录 PID，不创建凭据副本。所有写入在锁内重新读取，再原子更新 Keychain item；OAuth refresh 的读、决定、替换属于同一次排他操作。成功后发布原生变更通知，失败、取消或停用不发布成功事件。

每次读取重新访问 Keychain，不跨调用缓存秘密。锁定、拒绝、损坏、helper 失败均直接报错；不会尝试文件、环境变量或其他 Keychain。`describe` 和 `listRecords` 只返回配置状态、来源、地址与类型，不返回秘密。外部进程更新在下一次读取可见，本版本不主动监听 Keychain 外部变更事件。

helper 使用 Security.framework，秘密仅通过标准输入/输出传输，不进入命令参数或环境变量；错误仅含固定说明和 OSStatus。构建生成 arm64/x86_64 universal helper，目前仅使用本地 ad-hoc 签名。正式发布前必须验证发行签名、升级后的 Keychain ACL 和授权恢复。

## Model Experience

本包不注册 Agent 工具、提示词、后台任务或遥测。原生模型/插件通过已有 `ctx.credentials` 读取自己的引用，配置视图不会取得秘密值。

## Known Limitations and Deferred Work

Keychain 保护落盘秘密，不隔离受信任的同进程插件，也不能阻止同一用户的任意代码尝试调用 helper。个人来源、通用执行与委托的 OS 拒绝/撤销/旁路验收仍未完成。本包不代表 P1 整体通过。

目前仅验证临时 Keychain 与合成凭据，不访问登录 Keychain 的私人条目；真实 OAuth 厂商刷新、旧凭据迁移、正式签名及跨版本升级尚未验收。
