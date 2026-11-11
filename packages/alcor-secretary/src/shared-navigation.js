export function navigationGroups(applications, entries) {
  const own = new Set(applications.map(view => view.id))
  return [
    {label:'应用', items:applications},
    {label:'系统', items:entries.map(entry => entry.options).filter(row => !own.has(row.id)).map(row => ({
      id:row.id, label:typeof row.label==='function' ? row.label() : row.label ?? row.id,
    }))},
  ]
}
