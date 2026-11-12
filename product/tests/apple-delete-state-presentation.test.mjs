import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { reviewItems, resolutionRequest, deletionPhaseLabel } from '../../packages/alcor-apple-sync/src/presentation.js'

test('delete presentation separates pending, submitting and unknown without blind retry', () => {
  for (const [state,label] of [['pending','等待本次确认'],['sending','正在提交'],['uncertain','结果待核对']]) {
    const [item]=reviewItems({operations:[{id:'operation',action:'delete',state,document:{title:'Synthetic'}}]},'calendar')
    assert.ok(item.label.includes(label))
    if (state==='pending') assert.equal(resolutionRequest(item,'calendar','cancel').method,'cancel_operation')
    else assert.throws(()=>resolutionRequest(item,'calendar','cancel'),/不能取消或重发/)
  }
})

test('category wording does not ask the user to delete manually or retry unknown', () => {
  const source=readFileSync(new URL('../../packages/alcor-secretary/src/category-manager.jsx',import.meta.url),'utf8')
  assert.ok(source.includes('Alcor 将请求 Apple 删除'))
  assert.ok(source.includes('并在本机操作完成后处理本地记录'))
  assert.ok(!source.includes('失败会保留数据，可重试或取消'))
  assert.ok(source.includes("operation.state === 'pending' && <Button"))
  const expression=source.match(/^export const appleOperationMessage = ([\s\S]+?)\r?\n\r?\nconst colors/m)[1]
  const message=Function('return ('+expression+')')()
  assert.match(message({state:'pending',action:'delete'}),/等待本次确认/)
  assert.match(message({state:'sending'}),/正在向 Apple 提交/)
  assert.match(message({state:'uncertain'}),/不要重复提交/)
})

test('operation review distinguishes deletion approval from ordinary completion', () => {
  const source=readFileSync(new URL('../../packages/alcor-apple-sync/src/client.jsx',import.meta.url),'utf8')
  assert.ok(source.includes('Alcor 将请求 Apple 删除，并在本机操作完成后处理本地记录'))
  assert.ok(source.includes("pendingDelete ? deletionPhaseLabel(item) : '等待同步'"))
  assert.ok(!source.includes('下次同步会重试'))
  assert.ok(reviewItems({operations:[{id:'complete',action:'complete',state:'pending'}]},'reminders')[0].label.includes('等待同步'))
})

test('confirmed queued and strictly unsent keep cancellation but unknown never claims retry', () => {
  assert.match(deletionPhaseLabel({state:'pending',document:{_confirmed_intent:{version:1}}}),/已确认/)
  assert.match(deletionPhaseLabel({state:'pending',deletion_phase:'not_sent'}),/明确未发送/)
  for (const state of ['sending','uncertain']) {
    const item={state,deletion_phase:'not_sent',type:'operation'}
    assert.doesNotMatch(deletionPhaseLabel(item),/重试/)
    assert.throws(()=>resolutionRequest(item,'calendar','cancel'),/不能取消或重发/)
  }
})
