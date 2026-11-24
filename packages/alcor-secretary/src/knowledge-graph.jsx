import React, { useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import { Button, IconCloseOutlineRegular, IconFolderOpenRegular } from '@deepseek-ai/dsh-client-ui-primitives'
import { select as d3Select } from 'd3-selection'
import { drag } from 'd3-drag'
import { zoom, zoomIdentity } from 'd3-zoom'
import 'd3-transition'
import { forceCenter, forceSimulation, forceManyBody, forceLink, forceX, forceY, forceCollide } from 'd3-force'

const folderColors = ['#3f65ed', '#009984', '#8b53d9', '#df7426', '#548c32', '#ca4e89', '#1889bc', '#c29506', '#d85751', '#7168b7']
const folder = (node) => node.collection?.split('/')[0] || ''

// Adapted from CowAgent (MIT, Copyright 2022 zhayujie).
// https://github.com/zhayujie/CowAgent/blob/4d0ee4d2c5a709562551b8f209dfe679fa7c2094/channel/web/static/js/views/knowledge.js#L850-L999
function createLayout(data, showUnlinked, positions) {
  const byId = new Map(data.nodes.map((node) => [node.id, { ...node, ...positions?.get(node.id), degree: 0 }]))
  const edges = data.edges.map(({ source, target }) => {
    const from = byId.get(source), to = byId.get(target)
    from.degree++
    to.degree++
    return { source: from, target: to }
  })
  const nodes = [...byId.values()].filter((node) => showUnlinked || !edges.length || node.matched || node.degree)
  for (const node of nodes) {
    node.radius = Math.min(16, 5 + node.degree * 2)
    const letters = Array.from(node.title)
    node.label = letters.length > 15 ? letters.slice(0, 14).join('') + '…' : node.title
  }
  const simulation = forceSimulation(nodes).stop()
    .force('link', forceLink(edges).distance(90))
    .force('charge', forceManyBody().strength(-180))
    .force('center', forceCenter())
    .force('x', forceX().strength(0.06))
    .force('y', forceY().strength(0.06))
    .force('collision', forceCollide((node) => node.radius + 30))
  if (!positions) simulation.tick(300)
  return { nodes, edges, hidden: byId.size - nodes.length, simulation }
}

function fitTransform(canvas, nodes) {
  const { width, height } = canvas.getBoundingClientRect()
  const minX = Math.min(...nodes.map((node) => node.x))
  const maxX = Math.max(...nodes.map((node) => node.x))
  const minY = Math.min(...nodes.map((node) => node.y))
  const maxY = Math.max(...nodes.map((node) => node.y))
  const scale = Math.min(width / (maxX - minX + 32), height / (maxY - minY + 32), 4)
  return zoomIdentity.translate(width / 2 - (minX + maxX) / 2 * scale,
    height / 2 - (minY + maxY) / 2 * scale).scale(scale)
}

function nodePositions(nodes) {
  return new Map(nodes.map(({ id, x, y }) => [id, { x, y }]))
}

function renderPositions(model, positions) {
  if (!positions) return model
  const nodes = model.nodes.map((node) => ({ ...node, ...positions.get(node.id) }))
  const byId = new Map(nodes.map((node) => [node.id, node]))
  const edges = model.edges.map(({ source, target }) => ({ source: byId.get(source.id), target: byId.get(target.id) }))
  return { ...model, nodes, edges }
}

function GraphInspector({ graph, selected, onSelect, onClose, onOpen, onFocus, focusId, color, panel }) {
  const outgoing = [], incoming = []
  for (const { source, target } of graph.edges) {
    if (source.id === selected.id) outgoing.push(target)
    if (target.id === selected.id) incoming.push(source)
  }
  function list(items, follow = false) {
    return <ul>{items.map((node) => (
      <li key={node.id}><button type="button"
        title={follow && focusId !== node.id ? '查看「' + node.title + '」的局部图谱' : node.title}
        onClick={() => follow && focusId !== node.id ? onFocus(node) : onSelect(node.id, { center: true })}>
        <i style={{ background: color(node) }} /> <span>{node.title}</span>
      </button></li>
    ))}</ul>
  }
  return <aside ref={panel} className="alcor-graph-inspector" aria-label="资料关系">
    <div className="alcor-graph-inspector-heading">
      <h2>{selected.title}</h2>
      <Button size="sm" variant="ghost" aria-label="清除选择" title="清除选择" onClick={onClose}>
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

export function KnowledgeGraph({ data, onOpen, onFocus, focusId, camera, locateRef }) {
  const svg = useRef(null), panel = useRef(null), controls = useRef(null)
  const [view, setView] = useState(camera?.view ?? zoomIdentity)
  const [fitScale, setFitScale] = useState(camera?.fitScale ?? 1)
  const [selectedId, setSelectedId] = useState(camera ? camera.selectedId : focusId ?? null)
  const [hover, setHover] = useState(null)
  const [dragging, setDragging] = useState(false)
  const [showUnlinked, setShowUnlinked] = useState(camera?.showUnlinked ?? false)
  const model = useMemo(() => createLayout(data, showUnlinked, showUnlinked === camera?.showUnlinked ? camera.positions : null),
    [data, showUnlinked, camera])
  const [frame, setFrame] = useState(null)
  const graph = renderPositions(model, frame?.model === model ? frame.positions : null)
  const selected = graph.nodes.find((node) => node.id === selectedId)
  const active = hover?.id ?? selected?.id
  const related = new Set(active ? [active] : [])
  for (const { source, target } of graph.edges) {
    if (source.id === active) related.add(target.id)
    if (target.id === active) related.add(source.id)
  }
  const groups = useMemo(() => {
    const counts = new Map()
    for (const node of model.nodes) counts.set(folder(node), (counts.get(folder(node)) ?? 0) + 1)
    return [...counts.keys()].sort((a, b) => counts.get(b) - counts.get(a) || a.localeCompare(b))
  }, [model])
  const color = (node) => folderColors[groups.indexOf(folder(node)) % folderColors.length]
  const labelsVisible = view.k >= fitScale * 0.9
  const dotRadius = (node) => Math.min(node.radius, (5.5 + Math.sqrt(node.degree) * 2) / view.k)

  useLayoutEffect(() => {
    const element = svg.current
    if (!element) return
    const canvas = d3Select(element)
    const marks = canvas.selectAll('.alcor-graph-node').data(model.nodes)
    const behavior = zoom().on('zoom', (event) => setView(event.transform))
    const { simulation, nodes } = model

    function fit(animate = false) {
      const transform = fitTransform(element, nodes)
      if (!transform.k) return
      behavior.scaleExtent([Math.min(0.2, transform.k), 5])
      setFitScale(transform.k)
      canvas.interrupt()
      const motion = animate && !window.matchMedia('(prefers-reduced-motion: reduce)').matches
      const target = motion ? canvas.transition().duration(500) : canvas
      target.call(behavior.transform, transform)
    }

    canvas.call(behavior)
    if (camera?.view && showUnlinked === camera.showUnlinked) {
      behavior.scaleExtent([Math.min(0.2, camera.fitScale), 5])
      setFitScale(camera.fitScale)
      canvas.call(behavior.transform, zoomIdentity.translate(camera.view.x, camera.view.y).scale(camera.view.k))
    } else {
      fit()
    }
    const { width, height } = element.getBoundingClientRect()
    let lastWidth = width, lastHeight = height
    function resize() {
      const { width, height } = element.getBoundingClientRect()
      if (!width || !height || (Math.abs(width - lastWidth) < 0.5 && Math.abs(height - lastHeight) < 0.5)) return
      lastWidth = width
      lastHeight = height
      fit()
    }
    const observer = new ResizeObserver(resize)
    observer.observe(element)

    simulation.alphaDecay(0.1).on('tick', () => setFrame({ model, positions: nodePositions(nodes) }))
      .on('end', () => fit(true))
    marks.call(drag().clickDistance(3)
      .on('start', (event, node) => {
        canvas.interrupt()
        node.fx = node.x
        node.fy = node.y
      })
      .on('drag', (event, node) => {
        if (!simulation.alphaTarget()) simulation.alphaTarget(0.3).restart()
        node.fx = event.x
        node.fy = event.y
        setDragging(true)
        setHover(null)
      })
      .on('end', (event, node) => {
        if (!event.active) {
          simulation.alphaTarget(0)
          setDragging(false)
        }
        node.x = node.fx
        node.y = node.fy
        node.fx = null
        node.fy = null
      }))

    controls.current = {
      resize, fit,
      scaleBy: (factor) => canvas.interrupt().call(behavior.scaleBy, factor),
      pan: (x, y) => canvas.interrupt().call(behavior.translateBy, x / canvas.property('__zoom').k, y / canvas.property('__zoom').k),
      center: (node) => canvas.interrupt().call(behavior.translateTo, node.x, node.y),
    }
    return () => {
      observer.disconnect()
      simulation.stop().on('tick', null).on('end', null)
      canvas.interrupt().on('.zoom', null)
      marks.on('.drag', null)
      controls.current = null
    }
  }, [model, camera, showUnlinked])

  function select(id, { center = false, revealInspector = true } = {}) {
    // Measure the canvas and inspector after the selection changes their layout.
    flushSync(() => {
      setSelectedId(id)
      setHover(null)
    })
    controls.current?.resize()
    if (center) {
      const node = graph.nodes.find((item) => item.id === id)
      if (node) controls.current?.center(node)
    }
    if (id && revealInspector && panel.current && svg.current) {
      const below = panel.current.getBoundingClientRect().top >= svg.current.getBoundingClientRect().bottom - 1
      if (below) panel.current.scrollIntoView({ block: 'nearest' })
    }
  }
  function snapshot() {
    return { view, fitScale, selectedId, positions: nodePositions(model.nodes), showUnlinked }
  }
  function openFocus(node) {
    onFocus(node, snapshot())
  }
  useImperativeHandle(locateRef, () => (row) => {
    if (!graph.nodes.some((node) => node.id === row.id)) {
      openFocus(row)
      return
    }
    select(row.id, { center: true, revealInspector: false })
    svg.current?.querySelector(`[data-node-id="${CSS.escape(row.id)}"]`)?.focus({ preventScroll: true })
  })

  function hoverNode(event, node) {
    if (dragging || event.buttons) return
    const rect = svg.current.getBoundingClientRect()
    setHover({ id: node.id, node,
      x: Math.max(8, Math.min(event.clientX - rect.left + 12, rect.width - 248)),
      y: Math.max(8, Math.min(event.clientY - rect.top + 12, rect.height - 74)),
    })
  }
  if (!graph.nodes.length) return <p className="alcor-graph-empty" role="status">当前范围没有资料</p>
  return (
    <section className="alcor-library-graph" aria-label="知识图谱">
      <div className="alcor-graph-bar">
        <span>{graph.nodes.length} 份资料 · {graph.edges.length} 条引用</span>
        {!focusId && (graph.hidden > 0 || showUnlinked) && (
          <label><input type="checkbox" checked={showUnlinked} onChange={(event) => {
            setShowUnlinked(event.target.checked)
            setHover(null)
            if (selected && !selected.degree && !selected.matched) select(null)
          }} /> 显示未关联资料</label>
        )}
        <div className="alcor-graph-controls">
          <Button size="sm" variant="ghost" aria-label="缩小图谱" onClick={() => controls.current?.scaleBy(0.8)}>−</Button>
          <Button size="sm" variant="ghost" aria-label="放大图谱" disabled={view.k >= 5} onClick={() => controls.current?.scaleBy(1.25)}>＋</Button>
          <Button size="sm" variant="ghost" onClick={() => controls.current?.fit()}>适应窗口</Button>
        </div>
      </div>
      <div className="alcor-graph-body">
        <div className="alcor-graph-stage">
          <svg ref={svg} className="alcor-graph-canvas" aria-label="资料引用网络" role="group" tabIndex={0}
            aria-description="拖动空白平移。滚轮围绕光标缩放。单击节点或按回车、空格查看资料关系。"
            data-dragging={dragging || undefined} data-labels-visible={labelsVisible}
            onKeyDown={(event) => {
              const step = { ArrowLeft: [35, 0], ArrowRight: [-35, 0], ArrowUp: [0, 35], ArrowDown: [0, -35] }[event.key]
              if (step) controls.current?.pan(...step)
              else if (event.key === '0') controls.current?.fit()
              else if (['+', '=', '-'].includes(event.key)) controls.current?.scaleBy(event.key === '-' ? 0.8 : 1.25)
              else if (event.key === 'Escape') select(null)
              else return
              event.preventDefault()
            }}
            onClick={(event) => {
              if (!event.target.closest('[data-node-id]')) select(null)
            }}>
            <g transform={view.toString()}>
              {graph.edges.map(({ source, target }) => {
                const lit = source.id === active || target.id === active
                return <line key={source.id + ':' + target.id} x1={source.x} y1={source.y} x2={target.x} y2={target.y}
                  className="alcor-graph-edge" data-lit={lit} data-dim={!!active && !lit} />
              })}
              {graph.nodes.map((node) => (
                <g key={node.id} role="button" tabIndex={0} data-node-id={node.id}
                  aria-label={'查看资料关系：' + node.title} aria-pressed={selectedId === node.id}
                  className="alcor-graph-node" style={{ '--node-color': color(node) }}
                  data-active={node.id === active} data-matched={node.matched} data-dim={!!active && !related.has(node.id)}
                  transform={`translate(${node.x},${node.y})`}
                  onPointerEnter={(event) => hoverNode(event, node)} onPointerMove={(event) => hoverNode(event, node)}
                  onPointerLeave={() => setHover(null)}
                  onClick={() => select(node.id)}
                  onKeyDown={(event) => {
                    if (!['Enter', ' '].includes(event.key)) return
                    event.preventDefault()
                    select(node.id)
                  }}>
                  <circle r={Math.max(node.radius, 11 / view.k)} fill="transparent" className="alcor-graph-hit" />
                  <circle r={dotRadius(node)} vectorEffect="non-scaling-stroke" className="alcor-graph-dot" />
                  <circle r={dotRadius(node) * 0.55} className="alcor-graph-core" />
                  <circle r={dotRadius(node) + 3 / view.k} vectorEffect="non-scaling-stroke" className="alcor-graph-halo" />
                </g>
              ))}
              <g className="alcor-graph-labels" aria-hidden="true" visibility={labelsVisible ? 'visible' : 'hidden'}>
                {graph.nodes.map((node) => (
                  <text key={node.id} x={node.x} y={node.y} dx={dotRadius(node) + 6 / view.k} dy={3 / view.k}
                    fontSize={10 / view.k} data-dim={!!active && !related.has(node.id)}>{node.label}</text>
                ))}
              </g>
            </g>
          </svg>
          {hover && !dragging && <div className="alcor-graph-tooltip" aria-hidden="true" style={{ left: hover.x, top: hover.y }}>
            <strong>{hover.node.title}</strong>
            <span>{hover.node.collection?.replaceAll('/', ' / ') || '顶层'}</span>
          </div>}
        </div>
        {selected && <GraphInspector graph={graph} selected={selected} onSelect={select} panel={panel}
          onClose={() => {
            svg.current?.querySelector('[aria-pressed="true"]')?.focus()
            select(null)
          }}
          onOpen={(node) => onOpen(node, snapshot())} onFocus={openFocus}
          focusId={focusId} color={color} />}
      </div>
      <footer className="alcor-graph-legend">
        {groups.map((group, index) => <span key={group}><i style={{ background: folderColors[index % folderColors.length] }} />{group || '顶层'}</span>)}
        {!graph.edges.length && <span>在资料中插入引用即可建立关系</span>}
        {(data.total > data.nodes.length || data.more_edges) && <span>当前仅显示部分资料或引用，可按文件夹缩小范围</span>}
      </footer>
    </section>
  )
}
