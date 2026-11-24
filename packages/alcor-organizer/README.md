# Alcor organizer 开发源码

本目录是 `@alcor/secretary` 内 organizer 子插件的源码单元。构建入口为根目录 `npm run product:pack`，Host 源码打包到 `@alcor/secretary/organizer`，客户端所需 schema 与纯函数打包到秘书 Client。当前不单独发布本包，也不承诺 `lib` 独立产物。

开发实现仅覆盖新建本机记录、原生协作和定时的基础试验。原功能的一致性和迁移验收以 `product/feature-parity.md` 为准。

## 接入简报信息源

插件注册同一前缀的两个工具。用户在简报来源中启用相应的账号和范围后，手动及定时生成共用同一条采集路径。

- `<前缀>_briefing_sources` 返回 `{sources: [{id, account_id, label}]}`。每个目录项对应一个可选择的范围。
- `<前缀>_briefing_fetch` 接收 `source_id, account_id, since, until, offset, limit`，逐页返回 `{source_id, account_id, items, next_offset, errors}`。每页最多 100 条，最后一页令 `next_offset` 为 null。返回范围及账号须与请求一致，读取异常填入 errors。
- 每项必填稳定的 `source_id`、标题 `title` 和带时区的 `occurred_at`。可附发送者 `sender`、来源入口 `url`、真实发布时间 `published_at` 和修改时间 `source_updated_at`。同一项的更新沿用条目标识。
- 已能用程序完整呈现的事实放入 `payload.briefing_fact`（非空字符串），需语义判断的原文放入 `payload.body`。同一页可混合两类条目。保留段落、链接、条件和期限。来源尽可能提供全文，避免把截取正文得到的预览当作完整原文。
- 程序执行明确的用户规则并处理已确定的事实。剩余材料一次提交模型，以条目标识收回解读。模型结果缺项、重复或越界时不保存本轮简报，可以用同批采集结果重试。
