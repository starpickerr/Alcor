# 单 Agent 简报：合成评估

## 可复现入口

建议纳入仓库的最小集合：

- `scripts/product/evaluate-briefing-single-agent.py`
- `scripts/product/measure-briefing-transport.mjs`
- `product/tests/fixtures/briefing-single-agent-responses/`：三份独立模型合成响应与 `provenance.json`
- 本报告：`docs/briefing-single-agent-evaluation.md`

从仓库根运行，需已有项目 Node 依赖和本地基线 commit：

```sh
RUN=$(mktemp -d /private/tmp/stella-briefing-check-XXXXXX)
TMPDIR="$RUN" python3 -B scripts/product/evaluate-briefing-single-agent.py \
  --repo . --baseline 234e3bf3 \
  --responses product/tests/fixtures/briefing-single-agent-responses \
  --output "$RUN/result.json"
```

脚本自动用 `git archive` 从基线重建 business 快照，只读复制当前候选 business/相关 JS，使用独立 HOME、DSH_HOME、数据库和合成 Agent 执行，退出时删除临时源码和完整请求。仅保留指定的汇总 JSON；不读取个人 Profile、密钥或真实来源，也不调用 provider。无需把业务源码、完整请求或生成快照纳入仓库。

脚本验证四次连续运行、固定结果业务契约、独立响应的 schema/业务校验、原始输入/响应哈希、12项事实清单及排序。保存的独立输出仅复核，不会假装重新调用模型。

## 场景与结果

16条 fixture 覆盖六种来源、noreply、广告例外、来源限定规则、会议更正、长正文末尾截止、未知时间、缺失正文与注入文本。四次连续运行分别为首次生成、无变化、只改 meeting-new 的时间/地点、增加排除评分通知规则。

| 场景 | 基线→候选业务阶段 | prompt+结果schema估算 | 应用prompt/完整工具/同次历史估算 |
| --- | --- | --- | --- |
| 首次 | 2→1 | 7,949→3,352 | 8,055→3,445 |
| 无变化 | 0→0 | 0→0 | 0→0 |
| 单成员变更 | 2→2 | 1,503→1,503 | 1,610→2,181 |
| 规则变更 | 2→1 | 7,690→3,360 | 7,797→3,453 |

单成员变更重新筛选1条、重写完整2成员线程，其余11份报告保持不变。同一Agent保留历史增加第二阶段输入，不能宣称所有刷新都省token。规则变更使12张卡变为11张。

估算使用 DSH UTF-16长度/4计量器。应用估算来自候选真实 runAgent 的拼装与合成工具往返；不是原生宿主安全测试。未计宿主system/preset、历史工具声明事件、推理、缓存、压缩、失败重试和provider封装。**业务阶段数不等于实际provider请求数；真实usage、费用、时延均未测。** 固定回放只证明本样本的业务契约，不证明模型质量。

基线为已去重版本 `234e3bf3ba8ee60dc540ec573620cf691134cab8`。本轮worker生成src-UUID，不能直接与旧报告使用短ID的7,193相减。

## 独立模型输出：首次生成，一轮每版

三份响应分别来自未读标准答案的新上下文：基线筛选、基线写作、候选全局。它们不是已配置的DSH目标模型，没有目标provider usage。

- 两版均覆盖16个候选ID，形成同样12张卡，12项关键事实组通过核对，包含长正文末尾的截止/PDF与代码、会议取消改期、88元待支付、86/100与未知时间。
- 预设二元判断一致度为15/16与16/16。唯一差异是旧会议：基线过滤已被更正的通知，仍在新卡保留取消和改期，不可自动判为事实错误。
- 卡片成对排序63/66一致。候选把无具体截止的课程阅读提前到作业二、账单和比赛之前，不能称为排序完全不变。
- 未观察到执行注入或声称已回复、支付、创建日程/提醒。
- 独立输出compact JSON为7,771与7,294个UTF-16单位，正文为1,263与1,017；长度不代表质量或真实token。

响应生成于全局任务补入“不要重复每日大标题”之前。provenance保留原输入和响应哈希；验证只去掉这一后加短句来核对历史输入，不据此推断新提示词质量。未来fixture或其他prompt/schema改变会使哈希检查失败，应重新生成独立质量证据。

未独立评估增量/规则变更的模型质量；没有大输入分页效率、输出截断率或统计质量等效结论。

## 干净重建验证

从空运行目录执行上面的入口，四场景及两版独立输出校验全部通过，固定业务契约一致。最终冻结输入SHA-256：

- generator.py：`d42bcdf05793be3f864d05893b56173b19f8e5b501704a58f47e2d0d0ad6ee2d`
- worker.py：`b0c582692702059c4d6886947931d2a9008e79b598f8e16f14fe531b10270ed1`
- runs.py：`99e650b732c0a9b13a45eb81c4abad3559213a5126a015c7a09ba32f458910e8`
- briefing.js：`c51c77b1fc1a667994acc9c1e01d8d88ff1cb3071142bd1167d1a694cb9f40b6`
- fixture：`eb2aa09eea9375d191c74b3a71a82546104b93f0913d941b7d1971c3f6e6093a`

本次冻结包含提交前的运行状态/baseline/游标校验，以及最新取消请求持久化与提交先后语义更新。prompt/schema与计量数字未变。四场景、独立响应/provenance、12项事实和63/66排序检查已重新通过；本评估不替代取消/提交竞态的产品生命周期测试。哈希来自实际临时快照，后续改动需重跑。
