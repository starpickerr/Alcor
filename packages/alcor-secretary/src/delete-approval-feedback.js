// Host-authored messages are safe both in tool results and in searchable logs.
export const deleteApprovalReasons = new Map([
  ['DELETE_APPROVAL_DISABLED', '本对话已关闭审批。点输入框下的权限模式（如「完全权限」），选择「工作区内修改」，或输入 /permission workspace-write。重试后请核对删除对象，再点「允许一次」。'],
  ['DELETE_APPROVAL_UNAVAILABLE', '当前没有可用的审批服务或应答渠道。请在 DSH 桌面打开本对话后重试。'],
  ['DELETE_APPROVAL_REJECTED', '用户已拒绝本次删除。'],
  ['DELETE_APPROVAL_CANCELLED', '本次删除确认已取消。'],
  ['DELETE_APPROVAL_EXPIRED', '本次删除确认已失效。请重新核对目标后再发起删除。'],
])

export function denyDeleteApproval(code) {
  return { kind: 'deny', reason: deleteApprovalReasons.get(code), info: { code } }
}
