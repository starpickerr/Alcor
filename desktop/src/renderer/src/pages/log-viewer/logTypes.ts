export interface LogEntry {
  id: string
  timestamp: string
  level: string
  source: string
  component: string
  location: string
  message: string
  detail: string
}

export interface LogPage {
  entries: LogEntry[]
  next_cursor: string | null
  truncated: boolean
  sources: Record<string, number>
  scanned_bytes: number
}

export interface LogQuery {
  q?: string
  levels?: string
  source?: string
  since?: string
  cursor?: string
  limit?: string
}

export const sourceNames: Record<string, [string, string]> = {
  core: ['核心服务', 'Core'], agent: ['Agent 与工具', 'Agent & tools'], briefing: ['简报', 'Briefing'],
  tasks: ['事务与提醒', 'Tasks & reminders'], calendar: ['日历', 'Calendar'], scheduler: ['自动化', 'Automation'],
  email: ['邮箱', 'Email'], elearning: ['eLearning', 'eLearning'], data: ['数据', 'Data'],
  knowledge: ['知识', 'Knowledge'], memory: ['记忆', 'Memory'], security: ['权限与安全', 'Security'],
  bills: ['账单', 'Bills'], models: ['模型', 'Models'], channels: ['消息通道', 'Channels'],
  renderer: ['桌面界面', 'Desktop UI'], shell: ['启动与后端', 'Startup & backend'], updater: ['应用更新', 'App updates'],
}

export const sourceName = (source: string, en: boolean) => sourceNames[source]?.[en ? 1 : 0] || source
export const levelNames: Record<string, [string, string]> = {
  DEBUG: ['调试', 'Debug'], INFO: ['信息', 'Info'], WARNING: ['警告', 'Warning'],
  ERROR: ['错误', 'Error'], CRITICAL: ['严重', 'Critical'], UNKNOWN: ['未识别', 'Unknown'],
}
export const levelStyle = (level: string) => level === 'ERROR' || level === 'CRITICAL'
  ? 'text-red-700 dark:text-red-300 bg-danger-soft' : level === 'WARNING'
    ? 'text-amber-800 dark:text-amber-300 bg-amber-500/10' : level === 'DEBUG' || level === 'UNKNOWN'
      ? 'text-content-tertiary bg-surface-2' : 'text-accent bg-accent-soft'
