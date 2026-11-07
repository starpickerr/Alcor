import noticeStyle from '../../stella-secretary/src/notice.css'
import React from 'react'
import { remote } from './remote.js'
import { SchoolConnection } from '../../stella-secretary/src/connection-settings.jsx'
import style from './settings.css'
import connectionStyle from '../../stella-secretary/src/connection-settings.css'

export const inject = ['slots', 'remote']
function Settings({ api, view }) {
  if (view === 'summary') return '连接复旦课程、作业和公告。'
  return <section className="stella-source-settings"><h2>复旦 eLearning</h2><SchoolConnection api={api} /></section>
}
export async function apply(ctx) {
  ctx.effect(() => { const element = document.createElement('style'); element.textContent = style + connectionStyle + noticeStyle; document.head.append(element); return () => element.remove() })
  await ctx.remote.$mount(remote)
  ctx.inject(['remote.stellaSchool'], inner => inner.slots.inject('plugins.bundle.config', () => inner.slots.register({ name: 'plugins.bundle.config', key: '@stella/elearning', inject: () => ({ api: inner.remote.stellaSchool }) }, Settings)))
}
