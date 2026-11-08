import { taskGroup } from './presentation.js'

const groupOrder = ['overdue', 'today', 'upcoming', 'later', 'noDate', 'completed', 'cancelled']
export const taskKey = record => record.business?.occurrence_id ?? record.id

export function taskTree(records, matches, now) {
  const matched = new Set(matches.map(taskKey))
  const nodes = records.map(record => ({ key: taskKey(record), record, children: [], matched: matched.has(taskKey(record)) }))
  const byId = new Map()
  for (const node of nodes) if (!byId.has(node.record.id)) byId.set(node.record.id, node)
  const parents = new Map(nodes.map(node => [node, byId.get(node.record.business?.parent_id)]))
  // Imported records may contain broken links; keep every task reachable.
  for (const node of nodes) {
    const seen = new Set([node])
    let parent = parents.get(node)
    while (parent) {
      if (seen.has(parent)) { parents.delete(node); break }
      seen.add(parent); parent = parents.get(parent)
    }
  }
  const included = new Set()
  for (const node of nodes.filter(node => node.matched)) {
    let current = node
    while (current && !included.has(current)) { included.add(current); current = parents.get(current) }
  }
  const roots = []
  for (const node of nodes.filter(node => included.has(node))) {
    const parent = parents.get(node)
    if (parent) parent.children.push(node)
    else roots.push(node)
  }
  function summarize(node) {
    node.urgency = { overdue: 0, today: 0 }
    node.matchCount = Number(node.matched)
    node.group = node.matched ? taskGroup(node.record, now) : 'cancelled'
    for (const child of node.children) {
      summarize(child)
      node.matchCount += child.matchCount
      if (groupOrder.indexOf(child.group) < groupOrder.indexOf(node.group)) node.group = child.group
      for (const group of ['overdue', 'today']) node.urgency[group] += child.urgency[group] + Number(child.matched && taskGroup(child.record, now) === group)
    }
  }
  roots.forEach(summarize)
  return roots
}
