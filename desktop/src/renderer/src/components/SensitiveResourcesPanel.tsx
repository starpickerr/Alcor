import React from 'react'
import { Link } from 'react-router-dom'
import apiClient from '../api/client'
import { getLang } from '../i18n'
import { useSettingsLink, useRestoreScroll } from '../pages/settings/origin'
import { Feedback, useSecurityData } from './SecurityAuthorizationPanels'

const load = () => apiClient.sensitiveResources()
const card = 'min-w-0 rounded-card border border-default bg-surface p-5 sm:p-6'

export default function SensitiveResourcesPanel() {
  const en = getLang() === 'en'
  const openSettings = useSettingsLink()
  const state = useSecurityData(load)
  useRestoreScroll(!!state.data)
  return <div className="space-y-5 text-content">
    <Feedback {...state} />
    <section className={card}>
      <h2 className="text-base font-semibold text-content">{en ? 'Credential directory' : '凭据目录'}</h2>
      <p className="my-3 text-sm leading-6 text-content-secondary">{en ? 'View owner-reported status. Manage credentials through the original capability configuration; saved values are never shown here.' : '查看所属功能提供的凭据状态，前往原配置页面管理。此处不展示保存的密钥或密码。'}</p>
      {state.data?.credentials.length === 0 && <p className="text-sm text-content-secondary">{en ? 'No declared credentials.' : '暂无已声明的凭据。'}</p>}
      {state.data?.credentials.map(row => <div key={row.id} className="flex flex-wrap items-center justify-between gap-3 border-t border-default py-4">
        <div className="min-w-0 flex-1"><p className="break-all text-sm font-semibold">{row.account || row.owners.join(' · ')}</p>
          <p className="mt-1 break-all text-xs leading-5 text-content-secondary">{row.owners.join(' · ')} · {row.kind} · {row.configured === true ? (en ? 'Configured' : '已配置') : row.configured === false ? (en ? 'Not configured' : '未配置') : (en ? 'Status unavailable' : '状态暂不可用')}</p>
          {row.reason && <p className="mt-1 text-xs leading-5 text-content-tertiary">{row.reason}</p>}
        </div>
        {row.configure_url ? <Link className="inline-flex min-h-10 items-center text-sm text-accent" to={row.configure_url} onClick={event => openSettings(event, row.configure_url!)}>{en ? 'Open configuration' : '前往配置'}</Link> : <span className="text-xs text-content-tertiary">{en ? 'Configuration entry unavailable' : '配置入口不可用'}</span>}
      </div>)}
    </section>
    <section className={card}>
      <h2 className="text-base font-semibold text-content">{en ? 'Protected folders' : '受保护文件夹'}</h2>
      <p role="status" className="my-3 text-sm leading-6 text-content-secondary">{en ? 'Application-wide native protection is not ready. Folder rules cannot be activated; existing files and credentials have not been moved or deleted.' : '应用级原生保护尚未就绪，当前无法启用文件夹保护规则。已有文件与凭据保持原位。'}</p>
      <button type="button" disabled className="min-h-10 rounded-btn border border-default px-3 py-2 text-sm text-content-tertiary disabled:cursor-not-allowed">{en ? 'Add protected folder' : '添加受保护文件夹'}</button>
      <p className="mt-2 text-xs leading-5 text-content-tertiary">{en ? 'Requires a deployed and verified native protection component. A child-process sandbox does not cover Core and Electron.' : '需要已部署并验证的原生保护组件。子进程沙箱不能覆盖 Core 与 Electron。'}</p>
    </section>
  </div>
}
