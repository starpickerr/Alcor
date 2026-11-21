import React, { useEffect, useRef, useState } from 'react'
import {
  Button,
  Modal,
  IconDeliverDocRegular,
  IconPlusOutlineRegular,
} from '@deepseek-ai/dsh-client-ui-primitives'
import { KnowledgeGraph } from './knowledge-graph.jsx'
import { Notice } from './notice.jsx'
import { Empty } from './business-pages.jsx'

const labels = { note: '笔记', web: '网页', pdf: 'PDF', image: '图片', text: '文档' }
async function call(api, method, args, requestId) {
  const result = await api.business({
    domain: 'knowledge',
    method,
    arguments: args,
    ...(requestId ? { requestId } : {}),
  })
  if (!result.ok) throw new Error(result.error?.message || '知识库操作未完成，请重试。')
  return result.value
}
const message = (error) => error?.message || '读取失败，请重试。'

function readFile(file) {
  if (!file.size || file.size > 5 * 1024 * 1024) throw new Error('请选择 5 MB 以内的非空文件。')
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result.split(',')[1])
    reader.onerror = () => reject(new Error('无法打开所选文件。'))
    reader.readAsDataURL(file)
  })
}

function ReferencePicker({ api, onPick, exclude }) {
  const [query, setQuery] = useState(''),
    [items, setItems] = useState(null)
  const [error, setError] = useState(''),
    [retry, setRetry] = useState(0)
  useEffect(() => {
    let active = true
    setItems(null)
    setError('')
    const timer = setTimeout(
      () =>
        call(api, 'search', { query, grouped: true, limit: 8 })
          .then((data) => {
            if (active) setItems(data.items.filter((row) => row.id !== exclude))
          })
          .catch((reason) => {
            if (active) setError(message(reason))
          }),
      120
    )
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [api, query, exclude, retry])
  return (
    <div className="alcor-reference-picker">
      <input
        aria-label="查找要引用的资料"
        placeholder="查找要引用的资料"
        type="search"
        autoFocus
        maxLength={240}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      {error ? (
        <Notice tone="error">
          {error}{' '}
          <Button type="button" onClick={() => setRetry((n) => n + 1)}>
            重试
          </Button>
        </Notice>
      ) : !items ? (
        <small role="status">正在查找…</small>
      ) : items.length ? (
        items.map((row) => (
          <button type="button" key={row.id} onClick={() => onPick(row)}>
            <strong>{row.title}</strong>{' '}
            <small>
              {labels[row.kind]} · {row.snippet}
            </small>
          </button>
        ))
      ) : (
        <small>没有可引用的资料</small>
      )}
    </div>
  )
}

function DocumentText({ text, links, open, target, source, page }) {
  const mark =
    target?.snippet &&
    target.page === page &&
    target.revision === source.revision &&
    target.section === 'page'
  if (!mark && !links?.length) return text
  const letters = Array.from(text)
  let spans = links || []
  if (mark) {
    const end = target.start + Array.from(target.snippet).length
    // Reproduce a versioned citation exactly, even when its excerpt intersects a link.
    spans = spans.filter((span) => span.start >= end || span.end <= target.start)
    spans.push({ start: target.start, end })
    spans.sort((a, b) => a.start - b.start)
  }
  const parts = []
  let from = 0
  for (const span of spans) {
    parts.push(letters.slice(from, span.start).join(''))
    parts.push(
      span.id ? (
        <button
          type="button"
          className="alcor-knowledge-inline-link"
          key={span.start}
          onClick={() => open(span)}
        >
          {span.label}
        </button>
      ) : span.label ? (
        <span key={span.start} title={source?.historical ? '历史版本中的引用' : '此引用当前无法打开'}>
          {span.label}
        </span>
      ) : (
        <mark
          key={span.start}
          ref={(el) => {
            if (el && ['note', 'text', 'web'].includes(source.kind)) el.scrollIntoView({ block: 'nearest' })
          }}
        >
          {letters.slice(span.start, span.end).join('')}
        </mark>
      )
    )
    from = span.end
  }
  parts.push(letters.slice(from).join(''))
  return parts
}

function DocumentLinks({ api, id, open }) {
  const [offset, setOffset] = useState(0),
    [data, setData] = useState(null)
  const [error, setError] = useState(''),
    [retry, setRetry] = useState(0)
  useEffect(() => {
    let active = true
    setError('')
    setData(null)
    call(api, 'links', { id, offset })
      .then((rows) => {
        if (active) setData(rows)
      })
      .catch((reason) => {
        if (active) setError(message(reason))
      })
    return () => {
      active = false
    }
  }, [api, id, offset, retry])
  if (error)
    return (
      <Notice tone="error">
        {error} <Button onClick={() => setRetry((n) => n + 1)}>重试</Button>
      </Notice>
    )
  if (!data) return null
  return (
    <div className="alcor-document-links">
      {[
        ['outgoing', '引用了'],
        ['incoming', '被引用'],
      ].map(
        ([key, label]) =>
          data[key].length > 0 && (
            <section aria-label={label} key={key}>
              <h3>{label}</h3>
              {data[key].map((row) => (
                <Button size="sm" variant="outline" key={row.id} onClick={() => open(row)}>
                  {row.title}
                </Button>
              ))}
            </section>
          )
      )}
      {(offset > 0 || data.next_offset != null) && (
        <div className="alcor-knowledge-actions">
          <Button disabled={!offset} onClick={() => setOffset((n) => n - 25)}>
            上一组引用
          </Button>
          <Button disabled={data.next_offset == null} onClick={() => setOffset(data.next_offset)}>
            下一组引用
          </Button>
        </div>
      )}
    </div>
  )
}

function Editor({ api, source, file, collections = [], initialCollection = '', onClose, onSaved }) {
  const [title, setTitle] = useState(source?.title ?? file?.name ?? '')
  const [text, setText] = useState(source?.note ?? '')
  const [collection, setCollection] = useState(source?.collection ?? initialCollection)
  const [url, setUrl] = useState(source?.url ?? '')
  const [busy, setBusy] = useState(false),
    [error, setError] = useState('')
  const attempt = useRef(null),
    body = useRef(null)
  const [picking, setPicking] = useState(false)
  const insertion = useRef([0, 0])
  async function save(event) {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setError('')
    try {
      const args = { title, text, url, collection }
      if (source) Object.assign(args, { id: source.id, revision: source.revision })
      if (file) Object.assign(args, { filename: file.name, content: await readFile(file) })
      const draft = JSON.stringify([title, text, url, collection])
      if (attempt.current?.draft !== draft) attempt.current = { draft, key: crypto.randomUUID() }
      await call(api, source ? 'edit' : file ? 'import' : 'save', args, attempt.current.key)
      onSaved()
    } catch (reason) {
      setError(message(reason))
      setBusy(false)
    }
  }
  return (
    <Modal
      open
      title={source ? '编辑资料' : file ? '导入文件' : '收藏资料'}
      closeLabel="关闭"
      className="alcor-editor-dialog"
      onClose={() => {
        if (!busy) onClose()
      }}
    >
      <form className="alcor-knowledge-form" onSubmit={save}>
        <label>
          标题
          <input
            required
            autoFocus
            maxLength={240}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>
        <label>
          资料集
          <input
            list="alcor-library-collections"
            maxLength={100}
            placeholder="可选"
            value={collection}
            onChange={(e) => setCollection(e.target.value)}
          />
        </label>
        <datalist id="alcor-library-collections">
          {collections.map((name) => (
            <option key={name} value={name} />
          ))}
        </datalist>
        {!file && !source?.filename && (
          <label>
            来源网址
            <input
              type="url"
              placeholder="https://"
              maxLength={2048}
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
          </label>
        )}
        <label>
          {file || source?.filename || url ? '文字摘录' : '笔记'}
          <textarea
            ref={body}
            placeholder="输入正文，可用 [[资料标题]] 引用收藏"
            rows={6}
            maxLength={64000}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </label>
        <div className="alcor-reference-editor">
          <Button
            type="button"
            size="sm"
            variant="outline"
            aria-expanded={picking}
            onClick={() => {
              insertion.current = [body.current.selectionStart, body.current.selectionEnd]
              setPicking(!picking)
            }}
          >
            {picking ? '取消选择' : '插入资料引用'}
          </Button>
          {picking && (
            <ReferencePicker
              api={api}
              exclude={source?.id}
              onPick={(row) => {
                const [start, end] = insertion.current
                const label = row.title.replace(/[[\]\r\n]/g, ' ').trim() || '资料'
                const token = '[[id:' + row.id + '|' + label + ']]'
                const next = text.slice(0, start) + token + text.slice(end)
                if (next.length > 64000) {
                  setError('正文过长，请缩短后再插入引用。')
                  return
                }
                setText(next)
                setPicking(false)
                requestAnimationFrame(() => {
                  body.current?.focus()
                  body.current?.setSelectionRange(start + token.length, start + token.length)
                })
              }}
            />
          )}
        </div>
        {file && (
          <small>
            {file.name} · {(file.size / 1024).toFixed(0)} KB
          </small>
        )}
        {error && (
          <Notice role="alert" tone="error">
            {error}
          </Notice>
        )}
        <div className="alcor-knowledge-actions">
          <Button type="button" variant="outline" disabled={busy} onClick={onClose}>
            取消
          </Button>
          <Button
            type="submit"
            variant="primary"
            disabled={busy || !title.trim() || !(file || source?.filename || text.trim() || url.trim())}
          >
            {busy ? '保存中…' : '保存'}
          </Button>
        </div>
      </form>
    </Modal>
  )
}

function Original({ api, source }) {
  const [url, setUrl] = useState(''),
    [error, setError] = useState(''),
    [retry, setRetry] = useState(0)
  useEffect(() => {
    let active = true,
      objectUrl
    setUrl('')
    setError('')
    call(api, 'original', { id: source.id })
      .then((row) => {
        if (!active) return
        const type =
          row.kind === 'pdf'
            ? 'application/pdf'
            : row.kind === 'text'
              ? 'text/plain;charset=utf-8'
              : /\.png$/i.test(row.filename)
                ? 'image/png'
                : 'image/jpeg'
        const bytes = Uint8Array.from(atob(row.content), (c) => c.charCodeAt(0))
        objectUrl = URL.createObjectURL(new Blob([bytes], { type }))
        setUrl(objectUrl)
      })
      .catch((e) => {
        if (active) setError(message(e))
      })
    return () => {
      active = false
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [api, source.id, retry])
  if (error)
    return (
      <Notice tone="error">
        {error} <Button onClick={() => setRetry((n) => n + 1)}>重试</Button>
      </Notice>
    )
  if (!url) return <p role="status">正在读取原件…</p>
  return (
    <div className="alcor-knowledge-original">
      {source.kind === 'image' && <img src={url} alt={source.title} />}
      {source.kind === 'pdf' && (
        <object data={url + '#page=' + source.page} type="application/pdf" aria-label={source.title}>
          <p>请下载原件查看 PDF。</p>
        </object>
      )}
      <a href={url} download={source.filename}>
        下载原件
      </a>
    </div>
  )
}

function SourceReader({ target, ...props }) {
  const [trail, setTrail] = useState([])
  const current = trail.at(-1) || target
  return (
    <Reader
      key={current.id}
      {...props}
      target={current}
      onOpen={(row) => setTrail((items) => [...items, row])}
      back={trail.length ? () => setTrail((items) => items.slice(0, -1)) : null}
    />
  )
}

function Reader({ api, target, close, onChange, collections, onOpen, back }) {
  const [version, setVersion] = useState(target.revision)
  const [confirmErase, setConfirmErase] = useState(false)
  const [page, setPage] = useState(target.page || 1),
    [source, setSource] = useState(null)
  const [error, setError] = useState(''),
    [retry, setRetry] = useState(0),
    [busy, setBusy] = useState(false),
    [editing, setEditing] = useState(false)
  useEffect(() => {
    let active = true
    setError('')
    setSource(null)
    call(api, 'read', { id: target.id, page, include_links: true, ...(version ? { revision: version } : {}) })
      .then((row) => {
        if (active) setSource(row)
      })
      .catch((e) => {
        if (active) setError(message(e))
      })
    return () => {
      active = false
    }
  }, [api, target.id, page, retry, version])
  async function changeStatus(action) {
    setBusy(true)
    setError('')
    try {
      await call(api, action, { id: source.id, revision: source.revision })
      onChange?.()
      close()
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(false)
    }
  }
  if (confirmErase)
    return (
      <Modal
        open
        title="永久删除资料"
        closeLabel="取消删除"
        className="alcor-editor-dialog"
        onClose={() => {
          if (!busy) setConfirmErase(false)
        }}
      >
        <p>删除「{source.title}」及其历史版本？旧对话中的此条引用也将无法打开。</p>
        {error && (
          <Notice tone="error" role="alert">
            {error}
          </Notice>
        )}
        <div className="alcor-knowledge-actions">
          <Button disabled={busy} onClick={() => setConfirmErase(false)}>
            取消
          </Button>
          <Button variant="primary" disabled={busy} onClick={() => changeStatus('erase_archived')}>
            永久删除
          </Button>
        </div>
      </Modal>
    )
  if (editing)
    return (
      <Editor
        api={api}
        source={source}
        collections={collections}
        onClose={() => setEditing(false)}
        onSaved={() => {
          setEditing(false)
          setVersion(undefined)
          setRetry((n) => n + 1)
          onChange?.()
        }}
      />
    )
  return (
    <Modal
      open
      title={source?.title || target.title || '查看资料'}
      closeLabel="关闭"
      className="alcor-editor-dialog alcor-knowledge-dialog"
      onClose={() => {
        if (!busy) close()
      }}
    >
      {error && (
        <Notice tone="error" role="alert">
          {error} <Button onClick={() => setRetry((n) => n + 1)}>重新读取</Button>
        </Notice>
      )}
      {!source && !error && <p role="status">正在打开资料…</p>}
      {source && (
        <div className="alcor-knowledge-reader">
          <div className="alcor-knowledge-meta">
            {back && (
              <Button size="sm" variant="ghost" onClick={back}>
                ← 返回上一份资料
              </Button>
            )}
            <span>{labels[source.kind]}</span>
            {source.collection && <span>{source.collection}</span>}
            {source.url && (
              <a href={source.url} target="_blank" rel="noopener noreferrer">
                打开原网页 ↗
              </a>
            )}
            {source.page_count > 1 && (
              <div>
                <Button size="sm" disabled={page === 1} onClick={() => setPage((n) => n - 1)}>
                  上一页
                </Button>{' '}
                第 {page} / {source.page_count} 页{' '}
                <Button size="sm" disabled={page === source.page_count} onClick={() => setPage((n) => n + 1)}>
                  下一页
                </Button>
              </div>
            )}
          </div>
          {source.historical && (
            <Notice>
              这是引用时的版本。{' '}
              <Button size="sm" onClick={() => setVersion(undefined)}>
                查看当前版本
              </Button>
            </Notice>
          )}
          {source.filename && <Original api={api} source={source} />}
          {source.text && (
            <div className="alcor-knowledge-text">
              <DocumentText
                text={source.text}
                links={source.body_links}
                open={onOpen}
                target={target}
                source={source}
                page={page}
              />
            </div>
          )}
          {source.filename && source.note && (
            <blockquote>
              <DocumentText text={source.note} links={source.note_links} open={onOpen} />
            </blockquote>
          )}
          {!source.historical && (
            <DocumentLinks key={source.revision} api={api} id={source.id} open={onOpen} />
          )}
          {source.index_notice && <Notice>{source.index_notice}</Notice>}
          {!source.historical && (
            <div className="alcor-knowledge-actions">
              {!source.archived && (
                <Button variant="outline" onClick={() => setEditing(true)}>
                  编辑
                </Button>
              )}
              <Button
                variant="ghost"
                disabled={busy}
                onClick={() => changeStatus(source.archived ? 'restore' : 'archive')}
              >
                {source.archived ? '恢复收藏' : '归档'}
              </Button>
              {!!source.archived && (
                <Button variant="outline" disabled={busy} onClick={() => setConfirmErase(true)}>
                  永久删除
                </Button>
              )}
            </div>
          )}
        </div>
      )}
    </Modal>
  )
}

export function KnowledgeCitations({ turn, useKnowledgeCalls, api }) {
  const calls = useKnowledgeCalls(String(turn.turn))
  const [target, setTarget] = useState(null)
  const matches = new Map(),
    opened = new Map()
  for (const { root } of calls) {
    if (root.name !== 'alcor_knowledge' || root.isError || !root.content) continue
    let value
    try {
      value = JSON.parse(root.content.find((part) => part.type === 'text')?.text)
    } catch {
      continue
    }
    for (const item of value.items || (value.id ? [value] : [])) {
      const key = [item.id, item.revision, item.page || 1].join(':')
      const list = value.items ? matches : opened
      if (!list.has(key)) list.set(key, item)
    }
  }
  const sources = opened.size ? opened : matches
  if (!sources.size) return null
  return (
    <div
      className="alcor-knowledge-citations"
      role="group"
      aria-label={opened.size ? '知识库来源' : '知识库检索结果'}
    >
      {[...sources].slice(0, 5).map(([key, row]) => (
        <Button key={key} size="sm" variant="outline" onClick={() => setTarget(row)}>
          {row.title}
          {row.kind === 'pdf' && row.page ? ' · 第 ' + row.page + ' 页' : ''}
        </Button>
      ))}
      {target && <SourceReader api={api} target={target} close={() => setTarget(null)} />}
    </div>
  )
}

export function KnowledgePage({ api }) {
  const [query, setQuery] = useState(''),
    [archived, setArchived] = useState(false),
    [offset, setOffset] = useState(0)
  const [collections, setCollections] = useState([]),
    [collection, setCollection] = useState('')
  const [revision, setRevision] = useState(0),
    [result, setResult] = useState(null),
    [error, setError] = useState('')
  const [target, setTarget] = useState(null),
    [editor, setEditor] = useState(null)
  const picker = useRef(null)
  const [graph, setGraph] = useState(false),
    [graphQuery, setGraphQuery] = useState('')
  useEffect(() => {
    let active = true
    call(api, 'collections', {})
      .then((rows) => {
        if (active) setCollections(rows)
      })
      .catch((e) => {
        if (active) setError(message(e))
      })
    return () => {
      active = false
    }
  }, [api, revision])
  useEffect(() => {
    let active = true
    setResult(null)
    setError('')
    const timer = setTimeout(
      () =>
        call(api, graph ? 'graph' : 'search', {
          query: graph ? graphQuery : query,
          archived,
          offset,
          grouped: true,
          ...(collection ? { collection } : {}),
        })
          .then((data) => {
            if (active) setResult(data)
          })
          .catch((e) => {
            if (active) setError(message(e))
          }),
      (graph ? graphQuery : query) ? 150 : 0
    )
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [api, query, archived, offset, revision, collection, graph, graphQuery])
  function refresh() {
    setOffset(0)
    setRevision((n) => n + 1)
  }
  return (
    <main className="alcor-knowledge alcor-business">
      <header className="alcor-app-header">
        <h1>知识库</h1>
        <div className="alcor-knowledge-actions">
          <div className="alcor-library-views" role="group" aria-label="知识库视图">
            {[
              [false, '文档'],
              [true, '图谱'],
            ].map(([value, label]) => (
              <button
                key={label}
                type="button"
                aria-pressed={graph === value}
                onClick={() => {
                  if (value !== graph) {
                    setResult(null)
                    setGraph(value)
                  }
                }}
              >
                {label}
              </button>
            ))}
          </div>
          <Button variant="outline" onClick={() => picker.current.click()}>
            导入文件
          </Button>
          <Button variant="primary" onClick={() => setEditor({})}>
            <IconPlusOutlineRegular size={16} />
            收藏资料
          </Button>
        </div>
      </header>
      <input
        hidden
        type="file"
        ref={picker}
        accept=".pdf,.png,.jpg,.jpeg,.txt,.md"
        onChange={(e) => {
          const file = e.target.files[0]
          e.target.value = ''
          if (file) setEditor({ file })
        }}
      />
      <div className="alcor-page-content alcor-knowledge-content">
        <div className="alcor-knowledge-toolbar">
          <input
            type="search"
            aria-label={graph ? '筛选图谱标题' : '搜索知识库'}
            placeholder={graph ? '查找资料及其相邻引用' : '搜索标题、正文或摘录'}
            maxLength={240}
            value={graph ? graphQuery : query}
            onChange={(e) => {
              if (graph) setGraphQuery(e.target.value)
              else setQuery(e.target.value)
              setOffset(0)
            }}
          />
          {collections.length > 0 && (
            <select
              aria-label="筛选资料集"
              value={collection}
              onChange={(e) => {
                setCollection(e.target.value)
                setOffset(0)
              }}
            >
              <option value="">全部资料集</option>
              {collections.map((name) => (
                <option key={name}>{name}</option>
              ))}
            </select>
          )}
          {!graph && (
            <select
              aria-label="收藏范围"
              value={archived ? 'archived' : 'active'}
              onChange={(e) => {
                setArchived(e.target.value === 'archived')
                setOffset(0)
              }}
            >
              <option value="active">我的收藏</option>
              <option value="archived">已归档</option>
            </select>
          )}
        </div>
        {error ? (
          <Notice tone="error" role="alert">
            {error} <Button onClick={refresh}>重试</Button>
          </Notice>
        ) : !result ? (
          <p role="status">正在读取资料…</p>
        ) : graph ? (
          <KnowledgeGraph data={result} onOpen={setTarget} />
        ) : !result.items.length ? (
          <Empty
            title={query ? '没有找到匹配资料' : archived ? '还没有归档资料' : '还没有收藏资料'}
            icon={<IconDeliverDocRegular size={28} />}
          />
        ) : (
          <ul className="alcor-knowledge-list">
            {result.items.map((row) => (
              <li key={row.passage ?? row.id}>
                <button type="button" onClick={() => setTarget(row)}>
                  <span className="alcor-knowledge-type">{labels[row.kind]}</span>
                  <span>
                    <strong>{row.title}</strong>
                    {row.snippet && <small>{row.snippet}</small>}
                  </span>
                  {row.kind === 'pdf' && <small>第 {row.page} 页</small>}
                </button>
              </li>
            ))}
          </ul>
        )}
        {!graph && (offset > 0 || result?.next_offset != null) && (
          <div className="alcor-knowledge-actions">
            <Button disabled={!offset} onClick={() => setOffset((n) => Math.max(0, n - 25))}>
              上一批
            </Button>
            <Button disabled={result?.next_offset == null} onClick={() => setOffset(result.next_offset)}>
              下一批
            </Button>
          </div>
        )}
      </div>
      {editor && (
        <Editor
          {...editor}
          initialCollection={collection}
          collections={collections}
          api={api}
          onClose={() => setEditor(null)}
          onSaved={() => {
            setEditor(null)
            setQuery('')
            setCollection('')
            setArchived(false)
            refresh()
          }}
        />
      )}
      {target && (
        <SourceReader
          target={target}
          api={api}
          collections={collections}
          close={() => setTarget(null)}
          onChange={refresh}
        />
      )}
    </main>
  )
}
