import React, { useEffect, useId, useMemo, useRef, useState } from 'react'
import { Button, IconCloseOutlineRegular, IconFolderOpenRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { forceSimulation, forceManyBody, forceLink, forceX, forceY, forceCollide } from 'd3-force'

const folderColors = ['#4678ca', '#279386', '#a96c38', '#9766bd', '#b95b73', '#65883d', '#4c85a3']
const home = { x: 0, y: 0, zoom: 1 }
const folder = (node) => node.collection?.split('/')[0] || ''
const byDegree = (first, second) => second.degree - first.degree

function bounds(nodes) {
  if (!nodes.length) return { width: 0, height: 0, cx: 0, cy: 0 }
  const left = Math.min(...nodes.map((node) => node.x))
  const right = Math.max(...nodes.map((node) => node.x))
  const top = Math.min(...nodes.map((node) => node.y))
  const bottom = Math.max(...nodes.map((node) => node.y))
  return { width: right - left, height: bottom - top, cx: (left + right) / 2, cy: (top + bottom) / 2 }
}

function layout(data, showUnlinked) {
  const byId = new Map(data.nodes.map((node) => [node.id, { ...node, degree: 0 }]))
  const edges = data.edges.map(({ source, target }) => {
    const from = byId.get(source), to = byId.get(target)
    from.degree++
    to.degree++
    return { source: from, target: to }
  })
  const allNodes = [...byId.values()]
  const nodes = allNodes.filter((node) => showUnlinked || !edges.length || node.matched || node.degree)
  for (const node of nodes) {
    node.radius = 6 + Math.min(9, Math.sqrt(node.degree) * 2)
    const letters = Array.from(node.title)
    node.label = letters.length > 20 ? letters.slice(0, 19).join('') + '…' : node.title
  }
  forceSimulation(nodes)
    .stop()
    .force('links', forceLink(edges).distance(94).strength(0.6))
    .force('repel', forceManyBody().strength(-185))
    .force('x', forceX().strength(0.05))
    .force('y', forceY().strength(0.06))
    .force('spacing', forceCollide((node) => node.radius + 20))
    .tick(110)
  return { nodes, edges, hidden: allNodes.length - nodes.length, ...bounds(nodes) }
}

function placeNodes(graph, positions) {
  if (!positions.size) return graph
  const nodes = graph.nodes.map((node) => positions.has(node.id) ? { ...node, ...positions.get(node.id) } : node)
  const byId = new Map(nodes.map((node) => [node.id, node]))
  const edges = graph.edges.map(({ source, target }) => ({ source: byId.get(source.id), target: byId.get(target.id) }))
  return { ...graph, nodes, edges }
}

function graphLabels(nodes, frame, active, related) {
  const rank = (node) => node.id === active ? 3 : related.has(node.id) ? 2 : node.matched ? 1 : 0
  const points = nodes.map((node) => ({
    node,
    x: frame.x + node.x * frame.scale,
    y: frame.y + node.y * frame.scale,
    radius: node.radius * frame.scale,
  }))
  const overlaps = (a, b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top
  const labels = []
  for (const { node, x, y, radius } of [...points].sort((a, b) => rank(b.node) - rank(a.node) || byDegree(a.node, b.node))) {
    if ((active && !related.has(node.id)) || (frame.zoom < 0.9 && !rank(node))) continue
    if (x < 0 || y < 0 || x > frame.width || y > frame.height) continue
    const width = Array.from(node.label).reduce((sum, letter) => sum + (/^[ -~]$/.test(letter) ? 7 : 13), 6)
    const textGap = radius + 5
    const positions = [[x + textGap, y], [x - textGap - width, y], [x - width / 2, y + textGap + 7]]
    for (const [left, centerY] of positions) {
      const box = { left, right: left + width, top: centerY - 9, bottom: centerY + 9 }
      if (box.left < 4 || box.right > frame.width - 4 || box.top < 4 || box.bottom > frame.height - 4) continue
      if (labels.some((other) => overlaps(box, other))) continue
      if (node.id !== active && points.some((other) => other.node.id !== node.id && overlaps(box, {
        left: other.x - other.radius, right: other.x + other.radius,
        top: other.y - other.radius, bottom: other.y + other.radius,
      }))) continue
      labels.push({ ...box, y: centerY, node })
      break
    }
  }
  return labels
}

function GraphInspector({ graph, selected, onSelect, onOpen, onFocus, focusId, color, panel }) {
  const outgoing = [], incoming = []
  for (const { source, target } of graph.edges) {
    if (source.id === selected?.id) outgoing.push(target)
    if (target.id === selected?.id) incoming.push(source)
  }
  function list(items, follow = false) {
    return <ul>{items.map((node) => (
      <li key={node.id}><button type="button"
        title={follow && focusId !== node.id ? '查看「' + node.title + '」的局部图谱' : node.title}
        onClick={() => follow && focusId !== node.id ? onFocus(node) : onSelect(node.id, true)}>
        <i style={{ background: color(node) }} /> <span>{node.title}</span>
      </button></li>
    ))}</ul>
  }
  if (!selected) {
    const hubs = [...graph.nodes].sort(byDegree).slice(0, 6)
    return <aside ref={panel} className="alcor-graph-inspector" aria-label="资料关系">
      <h2>{graph.edges.length ? '关联最多的资料' : '当前资料'}</h2>
      {list(hubs)}
    </aside>
  }
  return <aside ref={panel} className="alcor-graph-inspector" aria-label="资料关系">
    <div className="alcor-graph-inspector-heading">
      <h2>{selected.title}</h2>
      <Button size="sm" variant="ghost" aria-label="清除选择" title="清除选择" onClick={() => onSelect(null)}>
        <IconCloseOutlineRegular size={14} aria-hidden="true" />
      </Button>
    </div>
    <p className="alcor-graph-location">
      <IconFolderOpenRegular size={14} aria-hidden="true" />
      {selected.collection?.replaceAll('/', ' / ') || '顶层'}
    </p>
    <div className="alcor-graph-inspector-actions">
      <Button size="sm" variant="primary" onClick={() => onOpen(selected)}>打开资料</Button>
      {focusId !== selected.id && <Button size="sm" variant="outline" onClick={() => onFocus(selected)}>查看局部图谱</Button>}
    </div>
    {[[outgoing, '引用了'], [incoming, '被引用']].map(([nodes, title]) => nodes.length > 0 && (
      <section key={title} aria-label={title}><h3>{title}<span>{nodes.length}</span></h3>{list(nodes, !!focusId)}</section>
    ))}
    {!outgoing.length && !incoming.length && <p className="alcor-graph-location">当前图谱中没有关联资料</p>}
  </aside>
}

export function KnowledgeGraph({ data, onOpen, onFocus, focusId, folders, camera }) {
  const arrow = useId()
  const svg = useRef(null), panel = useRef(null), gesture = useRef(null), ignoreClick = useRef(false)
  const [size, setSize] = useState({ width: 800, height: 440 })
  const [view, setView] = useState(camera?.view ?? home)
  const [fitBounds, setFitBounds] = useState(camera?.fitBounds ?? null)
  const [selectedId, setSelectedId] = useState(focusId ?? camera?.selectedId ?? null)
  const [positions, setPositions] = useState(() => camera?.positions ?? new Map())
  const [dragging, setDragging] = useState('')
  const [hovered, setHovered] = useState(null)
  const [showUnlinked, setShowUnlinked] = useState(camera?.showUnlinked ?? false)
  const base = useMemo(() => layout(data, showUnlinked), [data, showUnlinked])
  const graph = useMemo(() => placeNodes(base, positions), [base, positions])
  const area = fitBounds ?? base
  const selected = graph.nodes.find((node) => node.id === selectedId)
  const active = hovered ?? selected?.id
  const related = new Set(active ? [active] : [])
  for (const { source, target } of graph.edges) {
    if (source.id === active) related.add(target.id)
    if (target.id === active) related.add(source.id)
  }
  const groups = [...new Set(folders.map((path) => path.split('/')[0]))]
  const color = (node) => folder(node)
    ? folderColors[Math.max(0, groups.indexOf(folder(node))) % folderColors.length]
    : 'var(--dsw-alias-label-secondary)'
  const scale = view.zoom * Math.min(1.35, Math.max(1, size.width - 112) / Math.max(1, area.width),
    Math.max(1, size.height - 64) / Math.max(1, area.height))
  const x = size.width / 2 + view.x - area.cx * scale, y = size.height / 2 + view.y - area.cy * scale
  const labels = graph.nodes.length ? graphLabels(graph.nodes, { ...size, x, y, scale, zoom: view.zoom }, active, related) : []

  function zoom(factor, x = 0, y = 0) {
    setView((old) => {
      const next = Math.min(7, Math.max(0.45, old.zoom * factor))
      const ratio = next / old.zoom
      return { x: x + (old.x - x) * ratio, y: y + (old.y - y) * ratio, zoom: next }
    })
  }
  useEffect(() => {
    const canvas = svg.current
    if (!canvas) return
    function wheel(event) {
      if (!event.deltaY) return
      event.preventDefault()
      if (gesture.current) return
      const rect = canvas.getBoundingClientRect()
      zoom(Math.exp(-Math.sign(event.deltaY) * 0.12),
        event.clientX - rect.left - rect.width / 2, event.clientY - rect.top - rect.height / 2)
    }
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      if (width && height) setSize({ width, height })
    })
    observer.observe(canvas)
    canvas.addEventListener('wheel', wheel, { passive: false })
    return () => {
      observer.disconnect()
      canvas.removeEventListener('wheel', wheel)
    }
  }, [!!graph.nodes.length])

  function select(id, center = false) {
    setSelectedId(id)
    setHovered(null)
    if (center) {
      const node = graph.nodes.find((item) => item.id === id)
      if (node) setView({ x: (area.cx - node.x) * scale, y: (area.cy - node.y) * scale, zoom: view.zoom })
    }
    if (id && panel.current && svg.current) {
      const below = panel.current.getBoundingClientRect().top >= svg.current.getBoundingClientRect().bottom - 1
      if (below) panel.current.scrollIntoView({ block: 'nearest' })
    }
  }
  function fit() {
    setFitBounds(positions.size ? bounds(graph.nodes) : null)
    setView(home)
  }
  function startGesture(event) {
    if (event.button !== 0 || gesture.current) return
    const element = event.target.closest('[data-node-id]')
    const capture = element ?? event.currentTarget
    const node = graph.nodes.find((item) => item.id === element?.dataset.nodeId)
    ignoreClick.current = false
    gesture.current = {
      pointer: event.pointerId, clientX: event.clientX, clientY: event.clientY,
      node, x: node?.x ?? view.x, y: node?.y ?? view.y, scale, moved: false,
    }
    capture.setPointerCapture(event.pointerId)
  }
  function moveGesture(event) {
    const start = gesture.current
    if (!start || start.pointer !== event.pointerId) return
    const dx = event.clientX - start.clientX, dy = event.clientY - start.clientY
    if (!start.moved) {
      if (Math.hypot(dx, dy) < 5) return
      start.moved = true
      setDragging(start.node ? 'node' : 'canvas')
      setHovered(start.node?.id ?? null)
    }
    if (start.node) {
      const point = { x: start.x + dx / start.scale, y: start.y + dy / start.scale }
      setPositions((old) => new Map(old).set(start.node.id, point))
    } else {
      setView((old) => ({ ...old, x: start.x + dx, y: start.y + dy }))
    }
  }
  function endGesture(event) {
    if (gesture.current?.pointer !== event.pointerId) return
    ignoreClick.current = gesture.current.moved
    gesture.current = null
    setDragging('')
    if (ignoreClick.current) setHovered(null)
  }
  if (!graph.nodes.length) return <p className="alcor-graph-empty" role="status">当前范围没有资料</p>
  const visibleFolders = [...new Set(graph.nodes.map(folder))]
  return (
    <section className="alcor-library-graph" aria-label="知识图谱">
      <div className="alcor-graph-bar">
        <span>{graph.nodes.length} 份资料 · {graph.edges.length} 条引用</span>
        {!focusId && (graph.hidden > 0 || showUnlinked) && (
          <label><input type="checkbox" checked={showUnlinked} onChange={(event) => {
            setShowUnlinked(event.target.checked)
            setView(home)
            setFitBounds(null)
            if (selected && !selected.degree && !selected.matched) select(null)
          }} /> 显示未关联资料</label>
        )}
        <div className="alcor-graph-controls">
          <Button size="sm" variant="ghost" aria-label="缩小图谱" disabled={view.zoom <= 0.45} onClick={() => zoom(0.8)}>−</Button>
          <Button size="sm" variant="ghost" aria-label="放大图谱" disabled={view.zoom >= 7} onClick={() => zoom(1.25)}>＋</Button>
          <Button size="sm" variant="ghost" onClick={fit}>适应窗口</Button>
        </div>
      </div>
      <div className="alcor-graph-body">
        <svg ref={svg} className="alcor-graph-canvas" aria-label="资料引用网络" role="group" tabIndex={0}
          data-dragging={dragging || undefined}
          onKeyDown={(event) => {
            const step = { ArrowLeft: [35, 0], ArrowRight: [-35, 0], ArrowUp: [0, 35], ArrowDown: [0, -35] }[event.key]
            if (step) setView((old) => ({ ...old, x: old.x + step[0], y: old.y + step[1] }))
            else if (event.key === '0') fit()
            else if (['+', '=', '-'].includes(event.key)) zoom(event.key === '-' ? 0.8 : 1.25)
            else if (event.key === 'Escape') select(null)
            else return
            event.preventDefault()
          }}
          onPointerDown={startGesture} onPointerMove={moveGesture}
          onPointerUp={endGesture} onLostPointerCapture={endGesture}
          onClick={(event) => {
            if (!ignoreClick.current && !event.target.closest('[data-node-id]')) select(null)
          }}>
          <defs>
            <marker id={arrow} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="5" markerHeight="5" orient="auto">
              <path d="M0 0 L7 4 L0 8 Z" fill="var(--dsw-alias-label-primary)" />
            </marker>
          </defs>
          <g transform={`translate(${x},${y}) scale(${scale})`}>
            {graph.edges.map(({ source, target }) => {
              const dx = target.x - source.x, dy = target.y - source.y
              const length = Math.hypot(dx, dy) || 1
              const lit = source.id === active || target.id === active
              const gap = (target.radius + 2 / scale) / length
              return <line key={source.id + ':' + target.id} x1={source.x} y1={source.y}
                x2={target.x - dx * gap} y2={target.y - dy * gap}
                vectorEffect="non-scaling-stroke" markerEnd={lit ? `url(#${arrow})` : undefined}
                className="alcor-graph-edge" data-lit={lit} data-dim={!!active && !lit} />
            })}
            {graph.nodes.map((node) => (
              <g key={node.id} role="button" tabIndex={0} data-node-id={node.id}
                aria-label={'选择资料：' + node.title} aria-pressed={selectedId === node.id}
                className="alcor-graph-node" style={{ '--node-color': color(node) }}
                data-matched={node.matched} data-dim={!!active && !related.has(node.id)}
                transform={`translate(${node.x},${node.y})`}
                onPointerEnter={() => setHovered(node.id)} onPointerLeave={() => setHovered(null)}
                onFocus={() => setHovered(node.id)} onBlur={() => setHovered(null)}
                onClick={() => { if (!ignoreClick.current) select(node.id) }}
                onDoubleClick={() => { if (!ignoreClick.current) onOpen(node) }}
                onKeyDown={(event) => {
                  if (!['Enter', ' '].includes(event.key)) return
                  event.preventDefault()
                  if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) onOpen(node)
                  else select(node.id)
                }}>
                <title>{node.title} · {node.collection || '顶层'}</title>
                <circle r={Math.max(node.radius, 7 / scale)} fill="transparent" className="alcor-graph-hit" />
                <circle r={node.radius} vectorEffect="non-scaling-stroke" className="alcor-graph-dot" />
              </g>
            ))}
          </g>
          <g className="alcor-graph-labels" aria-hidden="true">
            {labels.map(({ node, left, y }) => (
              <text key={node.id} x={left} y={y} dominantBaseline="middle" data-active={node.id === active}>{node.label}</text>
            ))}
          </g>
        </svg>
        <GraphInspector graph={graph} selected={selected} onSelect={select} panel={panel}
          onOpen={onOpen} onFocus={(node) => onFocus(node, { view, fitBounds, selectedId, positions, showUnlinked })}
          focusId={focusId} color={color} />
      </div>
      <footer className="alcor-graph-legend">
        {visibleFolders.map((group) => <span key={group}><i style={{ background: color({ collection: group }) }} />{group || '顶层'}</span>)}
        {!graph.edges.length && <span>在资料中插入引用即可建立关系</span>}
        {(data.total > data.nodes.length || data.more_edges) && <span>当前仅显示部分资料或引用，请缩小搜索范围</span>}
      </footer>
    </section>
  )
}
