import React, {useSyncExternalStore} from 'react'
import {navigationGroups} from './shared-navigation.js'
import {BearLogo} from './bear-logo.jsx'
import {deskViews} from './desk.jsx'
import {IconPluginPinwheelOutlineRegular} from '@deepseek-ai/dsh-client-ui-primitives'

// This public footer seat leaves the official workspace and settings owners mounted.
export function SharedNavigation({wide, mode, panels, selectPanel, usePanelInfo}) {
  const activeMode = useSyncExternalStore(mode.subscribe, mode.getSnapshot)
  const activePanel = usePanelInfo(value => value.activePanelId)
  const entries = useSyncExternalStore(panels.subscribe, panels.getSnapshot)
  if (activeMode !== 'alcor') return null
  const applications = deskViews.map(view => ({...view, icon: view.icon ?? BearLogo}))
  return <nav className="alcor-shared-navigation" aria-label="Alcor 秘书导航">
    {navigationGroups(applications,entries).map(({label,items}) => <div className="alcor-nav-group" role="group" aria-label={label} key={label}>
      {wide && <p>{label}</p>}
      {items.map(({id,label,icon:Icon=IconPluginPinwheelOutlineRegular}) => <button key={id} type="button" title={label} aria-label={label}
        aria-current={activePanel===id ? 'page' : undefined} onClick={()=>selectPanel(id)}><Icon size={18}/>{wide && <span>{label}</span>}</button>)}
    </div>)}
  </nav>
}
