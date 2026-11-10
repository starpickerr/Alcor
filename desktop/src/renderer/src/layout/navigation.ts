import { MessageSquare, BookOpen, Brain, Zap, Radio, Clock, Settings, Sun, ScrollText, ListChecks, CalendarDays, Receipt, Database, Bot, Cpu } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

interface NavItem {
  path: string
  labelKey: string
  icon: LucideIcon
  pluginId?: string
  pageId?: string
}

interface NavGroup {
  id: string
  labelKey: string
  items: NavItem[]
}

export const NAV_GROUPS: NavGroup[] = [
  {
    id: 'chat',
    labelKey: 'nav_chat',
    items: [{ path: '/', labelKey: 'menu_chat', icon: MessageSquare }],
  },
  {
    id: 'apps',
    labelKey: 'nav_work',
    items: [
      { pluginId: 'stella.agent-hub', pageId: 'agent-hub.main', path: '/agent-hub', labelKey: 'menu_agent_hub', icon: Bot },
      { pluginId: 'stella.tasks', pageId: 'tasks.main', path: '/todos', labelKey: 'menu_todos', icon: ListChecks },
      { pluginId: 'stella.calendar', pageId: 'calendar.main', path: '/calendar', labelKey: 'menu_calendar', icon: CalendarDays },
      { pluginId: 'stella.briefing', pageId: 'briefing.main', path: '/briefing', labelKey: 'menu_briefing', icon: Sun },
      { pluginId: 'stella.bills', pageId: 'bills.main', path: '/bills', labelKey: 'menu_bills', icon: Receipt },
      { pluginId: 'stella.automation', pageId: 'automation.main', path: '/tasks', labelKey: 'menu_tasks', icon: Clock },
    ],
  },
  {
    id: 'info',
    labelKey: 'nav_info',
    items: [
      { path: '/data', labelKey: 'menu_data', icon: Database },
      { pluginId: 'stella.memory', pageId: 'memory.main', path: '/memory', labelKey: 'menu_memory', icon: Brain },
      { pluginId: 'stella.knowledge', pageId: 'knowledge.main', path: '/knowledge', labelKey: 'menu_knowledge', icon: BookOpen },
    ],
  },
  {
    id: 'channels',
    labelKey: 'nav_channels',
    items: [{ path: '/channels', labelKey: 'menu_channels', icon: Radio }],
  },
  {
    id: 'system',
    labelKey: 'nav_system',
    items: [
      { path: '/models', labelKey: 'settings_tab_models', icon: Cpu },
      { path: '/agent-settings', labelKey: 'config_agent', icon: Bot },
      { path: '/skills', labelKey: 'menu_evolution', icon: Zap },
      { path: '/general', labelKey: 'settings_tab_general', icon: Settings },
      { path: '/logs', labelKey: 'menu_logs', icon: ScrollText },
    ],
  },
]

const pluginPages = NAV_GROUPS.flatMap(group => group.items).filter(item => item.pluginId && item.pageId)
export const pluginPageBinding = (pluginId: string, pageId: string) => pluginPages.find(item => item.pluginId === pluginId && item.pageId === pageId)
