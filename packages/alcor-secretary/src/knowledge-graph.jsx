import React, { useEffect, useId, useMemo, useRef, useState } from 'react'
import { Button } from '@deepseek-ai/dsh-client-ui-primitives'
import { forceSimulation, forceManyBody, forceLink, forceX, forceY, forceCollide } from 'd3-force'

const kinds = { note: '笔记', web: '网页', pdf: 'PDF', image: '图片', text: '文档' }
const home = { x: 0, y: 0, zoom: 1 }

function layout(data) {
  const nodes = data.nodes.map((node) => ({ ...node, degree: 0 }))
  const byId = new Map(nodes.map((node) => [node.id, node]))
  const edges = data.edges.map((edge) => {
    const source = byId.get(edge.source),
      target = byId.get(edge.target)
    source.degree++
    target.degree++
    return { source, target }
  })
  for (const node of nodes) {
    node.radius = 7 + Math.min(9, Math.sqrt(node.degree) * 2)
    const letters = Array.from(node.title)
    node.label = letters.length > 14 ? letters.slice(0, 13).join('') + '…' : node.title
  }
  forceSimulation(nodes)
    .stop()
    .force('links', forceLink(edges).distance(108).strength(0.65))
    .force('repel', forceManyBody().strength(-230))
    .force('x', forceX().strength(0.055))
    .force('y', forceY().strength(0.055))
    .force(
      'labels',
      forceCollide((node) => 22 + Math.min(30, node.label.length * 2.5))
    )
    .tick(110)
  for (const edge of edges) {
    const dx = edge.target.x - edge.source.x,
      dy = edge.target.y - edge.source.y
    const length = Math.hypot(dx, dy) || 1
    edge.endX = edge.target.x - (dx / length) * (edge.target.radius + 2)
    edge.endY = edge.target.y - (dy / length) * (edge.target.radius + 2)
  }
  const minX = Math.min(...nodes.map((n) => n.x - 72)),
    maxX = Math.max(...nodes.map((n) => n.x + 72))
  const minY = Math.min(...nodes.map((n) => n.y - 24)),
    maxY = Math.max(...nodes.map((n) => n.y + 38))
  return {
    nodes,
    edges,
    width: maxX - minX,
    height: maxY - minY,
    cx: (maxX + minX) / 2,
    cy: (maxY + minY) / 2,
  }
}

export function KnowledgeGraph({ data, onOpen }) {
  const arrow = useId()
  const graph = useMemo(() => layout(data), [data])
  const svg = useRef(null),
    drag = useRef(null)
  const [size, setSize] = useState({ width: 800, height: 460 })
  const [view, setView] = useState(home),
    [focus, setFocus] = useState(null)
  useEffect(() => {
    if (!svg.current) return
    const canvas = svg.current
    function wheel(event) {
      event.preventDefault()
      const rect = canvas.getBoundingClientRect()
      const factor = Math.exp(-Math.sign(event.deltaY) * 0.12)
      zoom(factor, event.clientX - rect.left - rect.width / 2, event.clientY - rect.top - rect.height / 2)
    }
    canvas.addEventListener('wheel', wheel, { passive: false })
    const observer = new ResizeObserver(([entry]) => setSize(entry.contentRect))
    observer.observe(canvas)
    return () => {
      observer.disconnect()
      canvas.removeEventListener('wheel', wheel)
    }
  }, [!!graph.nodes.length])
  const scale = view.zoom * Math.min(1.3, size.width / graph.width, size.height / graph.height)
  function zoom(factor, x = 0, y = 0) {
    setView((old) => {
      const next = Math.min(8, Math.max(0.5, old.zoom * factor))
      const ratio = next / old.zoom
      return { x: x + (old.x - x) * ratio, y: y + (old.y - y) * ratio, zoom: next }
    })
  }
  const visible = new Set([focus])
  for (const { source, target } of graph.edges) {
    if (source.id === focus) visible.add(target.id)
    if (target.id === focus) visible.add(source.id)
  }
  if (!graph.nodes.length) return <p className="alcor-graph-empty">当前范围没有资料</p>
  return (
    <section className="alcor-library-graph" aria-label="知识图谱">
      <div className="alcor-graph-bar">
        <span>
          {graph.nodes.length} 份资料 · {graph.edges.length} 条引用
        </span>
        <div className="alcor-graph-controls">
          <Button
            size="sm"
            variant="ghost"
            aria-label="缩小图谱"
            disabled={view.zoom <= 0.5}
            onClick={() => zoom(0.8)}
          >
            −
          </Button>
          <Button
            size="sm"
            variant="ghost"
            aria-label="放大图谱"
            disabled={view.zoom >= 8}
            onClick={() => zoom(1.25)}
          >
            ＋
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setView(home)}>
            适应窗口
          </Button>
        </div>
      </div>
      <svg
        ref={svg}
        className="alcor-graph-canvas"
        aria-label="资料引用网络"
        role="group"
        tabIndex={0}
        onKeyDown={(e) => {
          if (['+', '=', '-', '0'].includes(e.key)) {
            e.preventDefault()
            if (e.key === '0') setView(home)
            else zoom(e.key === '-' ? 0.8 : 1.25)
            return
          }
          const step = { ArrowLeft: [35, 0], ArrowRight: [-35, 0], ArrowUp: [0, 35], ArrowDown: [0, -35] }[
            e.key
          ]
          if (step) {
            e.preventDefault()
            setView((v) => ({ ...v, x: v.x + step[0], y: v.y + step[1] }))
          }
        }}
        onPointerDown={(e) => {
          if (e.button !== 0 || e.target.closest('[role="button"]')) return
          e.currentTarget.setPointerCapture(e.pointerId)
          drag.current = { x: e.clientX - view.x, y: e.clientY - view.y }
        }}
        onPointerMove={(e) => {
          if (!drag.current) return
          const x = e.clientX - drag.current.x,
            y = e.clientY - drag.current.y
          setView((v) => ({ ...v, x, y }))
        }}
        onLostPointerCapture={() => {
          drag.current = null
        }}
      >
        <defs>
          <marker
            id={arrow}
            viewBox="0 0 8 8"
            refX="7"
            refY="4"
            markerWidth="6"
            markerHeight="6"
            orient="auto"
          >
            <path d="M0 0 L7 4 L0 8 Z" fill="var(--dsw-alias-label-secondary)" />
          </marker>
        </defs>
        <g
          transform={`translate(${size.width / 2 + view.x},${size.height / 2 + view.y}) scale(${scale}) translate(${-graph.cx},${-graph.cy})`}
        >
          {graph.edges.map(({ source, target, endX, endY }) => (
            <line
              key={source.id + target.id}
              x1={source.x}
              y1={source.y}
              x2={endX}
              y2={endY}
              markerEnd={`url(#${arrow})`}
              className="alcor-graph-edge"
              data-lit={source.id === focus || target.id === focus}
            />
          ))}
          {graph.nodes.map((node) => (
            <g
              key={node.id}
              role="button"
              tabIndex={0}
              aria-label={'打开资料：' + node.title}
              className="alcor-graph-node"
              data-kind={node.kind}
              data-dim={!!focus && !visible.has(node.id)}
              transform={`translate(${node.x},${node.y})`}
              onPointerEnter={() => setFocus(node.id)}
              onPointerLeave={() => setFocus(null)}
              onFocus={() => setFocus(node.id)}
              onBlur={() => setFocus(null)}
              onClick={() => onOpen(node)}
              onKeyDown={(e) => {
                if (['Enter', ' '].includes(e.key)) {
                  e.preventDefault()
                  onOpen(node)
                }
              }}
            >
              <title>
                {node.title} · {kinds[node.kind]}
              </title>
              <circle r={node.radius} />
              <text y={node.radius + 17} textAnchor="middle">
                {node.label}
              </text>
            </g>
          ))}
        </g>
      </svg>
      <footer className="alcor-graph-legend">
        {Object.entries(kinds)
          .filter(([kind]) => graph.nodes.some((n) => n.kind === kind))
          .map(([kind, label]) => (
            <span key={kind} data-kind={kind}>
              <i />
              {label}
            </span>
          ))}
      </footer>
      {(!graph.edges.length || data.total > graph.nodes.length || data.more_edges) && (
        <p className="alcor-graph-hint">
          {data.total > graph.nodes.length || data.more_edges
            ? '仅展示部分资料或引用，可按标题或资料集缩小范围。'
            : '在笔记或摘录中写入 [[资料标题]] 即可建立引用。'}
        </p>
      )}
    </section>
  )
}
