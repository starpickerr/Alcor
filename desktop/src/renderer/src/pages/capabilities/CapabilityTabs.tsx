export default function CapabilityTabs({ tabs, value, label, onChange }: {
  tabs: { id: string; label: string }[]; value: string; label: string; onChange: (id: string) => void
}) {
  return <div role="tablist" aria-label={label} className="flex gap-1 overflow-x-auto rounded-card bg-surface-2 p-1">
    {tabs.map((tab, index) => <button key={tab.id} id={`capability-tab-${tab.id}`} type="button" role="tab"
      aria-selected={value === tab.id} aria-controls="capability-content" tabIndex={value === tab.id ? 0 : -1}
      onClick={() => onChange(tab.id)} onKeyDown={event => {
        let next = index
        if (event.key === 'ArrowRight') next = (index + 1) % tabs.length
        else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length
        else if (event.key === 'Home') next = 0
        else if (event.key === 'End') next = tabs.length - 1
        else return
        event.preventDefault(); onChange(tabs[next].id)
        document.getElementById(`capability-tab-${tabs[next].id}`)?.focus()
      }} className={`shrink-0 rounded-btn px-4 py-2 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-accent ${value === tab.id ? 'bg-surface text-accent shadow-sm' : 'text-content-secondary hover:text-content'}`}>
      {tab.label}
    </button>)}
  </div>
}
