// Describe what survives navigation, not whether a particular account or job is active.
const both = '两边共用'
const alcor = 'Alcor'
const bench = 'DSH 工作台'
const onDemand = '按需调用，无独立后台任务'
const clientOnly = '仅界面显示，无后台任务'
const tasks = '已启动的任务继续执行'
const connection = '已配置的连接保持可用，按需读取'
const bundles = new Map(Object.entries({
  '@alcor/secretary': [alcor, '已开启的定时简报和主动交流继续运行'],
  '@alcor/agent-hub': [bench, '已启动的协作任务继续执行'],
  '@alcor/apple-sync': [alcor, '已开启的自动同步继续运行'],
  '@alcor/elearning': [both, connection],
  '@alcor/mail': [both, connection],
  '@alcor/organizer': [alcor, '已配置的事务和简报任务继续运行'],
  '@alcor/privacy': [both, '凭据服务继续按需响应'],
  '@alcor/plugin-info': [both, clientOnly],
  'dshmarket': [both, '市场服务保持可用'],
  'dsh-lan-memory': [both, '继续记录会话，按设置整理记忆'],
}))
const official = {
  'base': '宿主服务持续运行',
  'web-app': '客户端服务持续运行',
  'experimental-schedule-bundle': '已启用的计划继续按时执行',
  'personalized-recommendations': '收到新回复时生成推荐',
  'experimental-session-search': onDemand,
  'experimental-ralph-bundle': tasks,
  'experimental-terminal-bundle': '已启动的终端进程继续运行',
  'experimental-badge-skill-bundle': onDemand,
  'experimental-session-titles-bundle': '收到新消息时更新会话标题',
  'experimental-agent-team-profile': tasks,
  'experimental-voice-input-bundle': '使用语音输入时工作',
  'experimental-cot-translation-bundle': '展开推理内容时按需翻译',
  'experimental-auto-review': '需要工具审批时工作',
  'experimental-inspector-profile': '已启用的会话检查继续运行',
  'experimental-liveshare': '已开启的会话共享继续运行',
  'experimental-plugin-market-bundle': '打开市场或操作插件时工作',
  'experimental-tool-worktree': onDemand,
  'experimental-forge-bundle': '按需处理工作树和变更请求',
  'subagent-claude-code': tasks,
  'subagent-codex': tasks,
  'cua-bundle': onDemand,
}
for (const [name, background] of Object.entries(official)) {
  bundles.set('@deepseek-ai/dsh-' + name, [both, background])
}
const items = new Map(Object.entries({
  'shell': [both, '已启动的命令继续执行'],
  'subagent': [both, tasks],
  'agent-loop': [both, '进行中的对话继续执行'],
  'web-search': [both, onDemand],
}))
const unknown = ['范围未声明', '后台行为未声明']

export function pluginScope(name, {item = false, version} = {}) {
  if (!item && name === 'dsh-onebot-qq' && version?.includes('-alcor.')) {
    return [alcor, '已连接的 QQ 继续收发消息']
  }
  return (item ? items : bundles).get(name) ?? unknown
}
