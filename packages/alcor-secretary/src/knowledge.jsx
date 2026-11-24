import React, { useEffect, useId, useRef, useState } from 'react'
import {
  Button,
  Modal,
  IconChevronDownOutlineRegular,
  IconChevronRightOutlineRegular,
  IconCloseOutlineRegular,
  IconDeliverDocRegular,
  IconDownloadOutlineRegular,
  IconEditOutlineRegular,
  IconFolderOpenRegular,
  IconPlusOutlineRegular,
  IconProjectAddOutlineRegular,
  IconTrashOutlineRegular,
} from '@deepseek-ai/dsh-client-ui-primitives'
import { KnowledgeGraph } from './knowledge-graph.jsx'
import { Notice } from './notice.jsx'
import { Empty } from './business-pages.jsx'

const labels = { note: '笔记', web: '网页', pdf: 'PDF', image: '图片', text: '文档' }
const parentFolder = (path) => path.slice(0, Math.max(0, path.lastIndexOf('/')))
const folderName = (path) => path.slice(path.lastIndexOf('/') + 1) || path
const displayLocation = (path) => path ? path.replaceAll('/', ' / ') : '顶层'
const inFolder = (path, folder) => path === folder || path.startsWith(folder + '/')

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

function GraphTitleFinder({ api, collection, revision, onLocate, disabled }) {
  const listId = useId()
  const list = useRef(null)
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const [result, setResult] = useState(null)
  const term = query.trim()
  const show = open && !!term && !disabled
  const key = JSON.stringify([collection, revision, term])
  const current = result?.key === key ? result : null
  const items = current?.items ?? []
  const choice = items[Math.min(active, items.length - 1)]
  const optionId = (row) => listId + '-' + row.id
  useEffect(() => {
    if (!show) return
    let live = true
    const timer = setTimeout(() => {
      call(api, 'graph_titles', { query: term, collection: collection || null }).then((rows) => {
        if (live) setResult({ key, items: rows })
      }).catch((error) => {
        if (live) setResult({ key, error: message(error) })
      })
    }, 140)
    return () => { live = false; clearTimeout(timer) }
  }, [api, show, term, collection, key])

  function pick(row) {
    setOpen(false)
    onLocate(row)
  }
  function keys(event) {
    if (event.nativeEvent.isComposing || event.keyCode === 229 || !show) return
    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      setOpen(false)
    } else if (event.key === 'Enter' && choice) {
      event.preventDefault()
      pick(choice)
    } else if (['ArrowDown', 'ArrowUp'].includes(event.key) && items.length) {
      event.preventDefault()
      const index = Math.min(active, items.length - 1)
      const next = (index + (event.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length
      setActive(next)
      list.current?.children[next]?.scrollIntoView({ block: 'nearest' })
    }
  }
  return (
    <div className="alcor-graph-finder" onBlur={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false)
    }}>
      <input type="search" role="combobox" aria-label="在图谱中查找资料" aria-autocomplete="list"
        aria-expanded={show && items.length > 0}
        aria-controls={show && items.length ? listId : undefined}
        aria-activedescendant={show && choice ? optionId(choice) : undefined}
        placeholder="输入标题定位资料" maxLength={240} value={query} disabled={disabled}
        onFocus={() => setOpen(true)} onClick={() => setOpen(true)} onKeyDown={keys}
        onChange={(event) => {
          setQuery(event.target.value)
          setActive(0)
          setOpen(true)
        }} />
      {show && <div className="alcor-graph-matches">
        {items.length ? (
          <ul id={listId} ref={list} role="listbox" aria-label="匹配的资料">
            {items.map((row) => (
              <li key={row.id} id={optionId(row)} role="option" aria-selected={row === choice}
                onMouseDown={(event) => event.preventDefault()} onClick={() => pick(row)}>
                <strong title={row.title}>{row.title}</strong>
                <small title={displayLocation(row.collection)}>{displayLocation(row.collection)}</small>
              </li>
            ))}
          </ul>
        ) : <p role="status">{current?.error ?? (current ? '没有匹配的资料' : '正在查找…')}</p>}
      </div>}
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

function FolderLocationSelect({ folders, value, onChange }) {
  const locations = value && !folders.includes(value) ? [...folders, value] : folders
  return (
    <label>
      保存位置
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">顶层</option>
        {locations.map((path) => <option key={path} value={path}>{displayLocation(path)}</option>)}
      </select>
    </label>
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
      const saved = await call(api, source ? 'edit' : file ? 'import' : 'create_note', args, attempt.current.key)
      onSaved(collection, saved)
    } catch (reason) {
      setError(message(reason))
      setBusy(false)
    }
  }
  return (
    <Modal
      open
      title={source ? '编辑资料' : file ? '导入文件' : '新建文件'}
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
        <FolderLocationSelect folders={collections} value={collection} onChange={setCollection} />
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
            disabled={busy || !title.trim()}
          >
            {busy ? '保存中…' : '保存'}
          </Button>
        </div>
      </form>
    </Modal>
  )
}

function useOriginal(api, id) {
  const [url, setUrl] = useState(''),
    [error, setError] = useState(''),
    [retry, setRetry] = useState(0)
  useEffect(() => {
    let active = true,
      objectUrl
    setUrl('')
    setError('')
    if (!id) return
    call(api, 'original', { id })
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
  }, [api, id, retry])
  return { url, error, retry: () => setRetry((n) => n + 1) }
}

function Original({ source, original }) {
  const { url, error, retry } = original
  if (error)
    return (
      <Notice tone="error">
        {error} <Button onClick={retry}>重试</Button>
      </Notice>
    )
  if (!url) return <p role="status">正在读取原件…</p>
  if (source.kind === 'text') return null
  return (
    <div className="alcor-knowledge-original">
      {source.kind === 'image' && <img src={url} alt={source.title} />}
      {source.kind === 'pdf' && (
        <object data={url + '#page=' + source.page} type="application/pdf" aria-label={source.title}>
          <p>请下载原件查看 PDF。</p>
        </object>
      )}
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

function DeleteLibraryEntry({ api, entry, onClose, onDeleted }) {
  const [plan, setPlan] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const path = entry.path
  useEffect(() => {
    if (!path) return
    let active = true
    call(api, 'plan_folder_delete', { path }).then((result) => {
      if (active) setPlan(result)
    }).catch((reason) => {
      if (active) setError(message(reason))
    })
    return () => { active = false }
  }, [api, path])
  async function erase() {
    if (busy || error || (path && !plan)) return
    setBusy(true)
    try {
      if (path) await call(api, 'delete_folder', { path, fingerprint: plan.fingerprint })
      else await call(api, 'delete', { id: entry.id, revision: entry.revision })
      onDeleted()
    } catch (reason) {
      setError(message(reason))
      setBusy(false)
    }
  }
  return (
    <Modal open title={path ? '永久删除文件夹' : '永久删除资料'}
      closeLabel="取消删除" className="alcor-editor-dialog" onClose={() => !busy && onClose()}>
      {path ? <>
        <p>删除「{displayLocation(path)}」？</p>
        {plan ? <p>其中 {plan.documents} 份资料和 {plan.folders} 个子文件夹也会一并删除。</p>
          : !error && <p role="status">正在检查文件夹内容…</p>}
      </> : <p>删除「{entry.title}」及其历史版本？旧对话中的此条引用也将无法打开。</p>}
      {error && <Notice tone="error" role="alert">{error}</Notice>}
      <div className="alcor-knowledge-actions">
        <Button variant="outline" disabled={busy} onClick={onClose}>取消</Button>
        <Button variant="primary" disabled={busy || !!error || (!!path && !plan)} onClick={erase}>
          {busy ? '删除中…' : '永久删除'}
        </Button>
      </div>
    </Modal>
  )
}

function Reader({ api, target, close, onChange, collections, onOpen, back, inline, libraryRevision }) {
  const [version, setVersion] = useState(inline ? undefined : target.revision)
  const [confirmErase, setConfirmErase] = useState(false)
  const [page, setPage] = useState(target.page || 1),
    [source, setSource] = useState(null)
  const [error, setError] = useState(''),
    [retry, setRetry] = useState(0),
    [editing, setEditing] = useState(false)
  const original = useOriginal(api, source?.filename ? source.id : undefined)
  useEffect(() => setSource(null), [api, target.id, page, version])
  useEffect(() => {
    let active = true
    setError('')
    call(api, 'read', { id: target.id, page, include_links: true, ...(version ? { revision: version } : {}) })
      .then((row) => {
        if (active) setSource(row)
      })
      .catch((e) => {
        if (active) {
          setSource(null)
          setError(message(e))
        }
      })
    return () => {
      active = false
    }
  }, [api, target.id, page, retry, version, libraryRevision])
  if (confirmErase && source)
    return <DeleteLibraryEntry api={api} entry={source} onClose={() => setConfirmErase(false)} onDeleted={() => {
      onChange?.()
      close()
    }} />
  if (editing)
    return (
      <Editor
        api={api}
        source={source}
        collections={collections}
        onClose={() => setEditing(false)}
        onSaved={(_, saved) => {
          setEditing(false)
          setVersion(undefined)
          setRetry((n) => n + 1)
          onChange?.(saved)
        }}
      />
    )
  const title = source?.title || target.title || '查看资料'
  const actions = source && (source.filename || source.url || !source.historical) && (
    <div className="alcor-library-tools" role="group" aria-label="资料操作">
      {source.filename && (original.url ? (
        <a className="alcor-library-icon-link" href={original.url} download={source.filename} aria-label="下载原件" title="下载原件">
          <IconDownloadOutlineRegular size={18} aria-hidden="true" />
        </a>
      ) : <Button size="sm" variant="ghost" disabled aria-label="下载原件" title="下载原件">
        <IconDownloadOutlineRegular size={18} aria-hidden="true" />
      </Button>)}
      {source.url && <a className="alcor-library-web-link" href={source.url} target="_blank" rel="noopener noreferrer">打开原网页 ↗</a>}
      {!source.historical && <>
        <Button type="button" size="sm" variant="ghost" aria-label="编辑" title="编辑" onClick={() => setEditing(true)}>
          <IconEditOutlineRegular size={18} aria-hidden="true" />
        </Button>
        <Button type="button" size="sm" variant="ghost" aria-label="删除资料" title="删除资料" onClick={() => setConfirmErase(true)}>
          <IconTrashOutlineRegular size={18} aria-hidden="true" />
        </Button>
      </>}
    </div>
  )
  const content = (
    <>
      {!inline && actions}
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
            <span className="alcor-library-location">
              <span>{labels[source.kind]}</span>
              {source.collection.split('/').filter(Boolean).map((folder, index) => (
                <React.Fragment key={index}>
                  <IconChevronRightOutlineRegular size={12} aria-hidden="true" />
                  <span>{folder}</span>
                </React.Fragment>
              ))}
            </span>
            {source.page_count > 1 && (
              <div className="alcor-library-pages" role="group" aria-label="文档分页">
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
          {source.filename && <Original source={source} original={original} />}
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
        </div>
      )}
    </>
  )
  if (inline) return (
    <article aria-label="资料详情" className="alcor-library-document">
      <header><h2>{title}</h2>{actions}</header>
      {content}
    </article>
  )
  return (
    <Modal open title={title} closeLabel="关闭" className="alcor-editor-dialog alcor-knowledge-dialog"
      onClose={close}>{content}</Modal>
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

function LibraryIconButton({ icon: Icon, label, title = label, onClick }) {
  return <Button type="button" size="sm" variant="ghost" aria-label={label} title={title}
    onClick={onClick}>
    <Icon size={16} aria-hidden="true" />
  </Button>
}

function LibraryActions({ className, name, onCreateFile, onCreateFolder, onRename, onDelete }) {
  return <div className={className} role="group" aria-label={name ? name + '的操作' : '目录操作'}>
    <LibraryIconButton icon={IconPlusOutlineRegular} label={name ? '在' + name + '中新建文件' : '新建文件'}
      title="新建文件" onClick={onCreateFile} />
    <LibraryIconButton icon={IconProjectAddOutlineRegular} label={name ? '在' + name + '中新建文件夹' : '新建文件夹'}
      title="新建文件夹" onClick={onCreateFolder} />
    {onRename && <LibraryIconButton icon={IconEditOutlineRegular} label={'重命名文件夹：' + name}
      title="重命名文件夹" onClick={onRename} />}
    {onDelete && <LibraryIconButton icon={IconTrashOutlineRegular} label={'删除文件夹：' + name}
      title="删除文件夹" onClick={onDelete} />}
  </div>
}

function FolderDocuments({ api, path, revision, renderDocument, onRetry }) {
  const [offset, setOffset] = useState(0)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    setError('')
    call(api, 'search', { collection: path, offset }).then((data) => {
      if (!active) return
      if (offset && !data.items.length) { setOffset(0); return }
      setResult(data)
    }).catch((reason) => { if (active) setError(message(reason)) })
    return () => { active = false }
  }, [api, path, revision, offset])
  if (error) return <li role="alert">{error} <Button size="sm" onClick={onRetry}>重试</Button></li>
  if (!result) return <li className="alcor-library-status" role="status">正在读取…</li>
  return <>
    {result.items.map(renderDocument)}
    {(offset > 0 || result.next_offset != null) && <li className="alcor-library-pagination">
      <Button size="sm" disabled={!offset} onClick={() => setOffset((n) => Math.max(0, n - 25))}>上一批</Button>
      <Button size="sm" disabled={result.next_offset == null} onClick={() => setOffset(result.next_offset)}>下一批</Button>
    </li>}
  </>
}

function LibraryNameDialog({ api, entry, folders, onClose, onSaved }) {
  const creating = entry.parent !== undefined
  const oldName = entry.path ? folderName(entry.path) : entry.title || ''
  const [name, setName] = useState(oldName)
  const [parent, setParent] = useState(entry.parent)
  const title = creating ? '新建文件夹' : entry.path ? '重命名文件夹' : '重命名文件'
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const canSave = !busy && !!name.trim() && name.trim() !== oldName
  async function save(event) {
    event.preventDefault()
    if (!canSave) return
    setBusy(true)
    setError('')
    try {
      let saved
      if (creating) saved = await call(api, 'create_folder', { parent, name })
      else if (entry.path) saved = await call(api, 'rename_folder', { path: entry.path, name })
      else saved = await call(api, 'rename', { id: entry.id, revision: entry.revision, name })
      onSaved(saved)
    } catch (reason) {
      setError(message(reason))
      setBusy(false)
    }
  }
  return (
    <Modal open title={title} closeLabel="关闭" className="alcor-editor-dialog" onClose={() => !busy && onClose()}>
      <form className="alcor-knowledge-form" onSubmit={save}>
        <label>{entry.id ? '文件名称' : '文件夹名称'}<input required autoFocus maxLength={entry.id ? 240 : 100}
          value={name} onFocus={(event) => event.target.select()} onChange={(event) => setName(event.target.value)} /></label>
        {creating && <FolderLocationSelect folders={folders} value={parent} onChange={setParent} />}
        {error && <Notice role="alert" tone="error">{error}</Notice>}
        <div className="alcor-knowledge-actions">
          <Button type="button" variant="outline" disabled={busy} onClick={onClose}>取消</Button>
          <Button type="submit" variant="primary" disabled={!canSave}>{creating ? '新建' : '保存'}</Button>
        </div>
      </form>
    </Modal>
  )
}

export function KnowledgePage({ api }) {
  const [query, setQuery] = useState(''),
    [offset, setOffset] = useState(0)
  const [collections, setCollections] = useState([]),
    [collection, setCollection] = useState('')
  const [revision, setRevision] = useState(0),
    [result, setResult] = useState(null),
    [error, setError] = useState('')
  const [target, setTarget] = useState(null),
    [editor, setEditor] = useState(null)
  const [naming, setNaming] = useState(null)
  const [deleting, setDeleting] = useState(null)
  const [expanded, setExpanded] = useState(new Set())
  const [movePending, setMovePending] = useState(false)
  const [moveMessage, setMoveMessage] = useState(null)
  const [dropTarget, setDropTarget] = useState(null)
  const [showTopDrop, setShowTopDrop] = useState(false)
  const dragging = useRef(null)
  const moveInFlight = useRef(false)
  const detail = useRef(null)
  const picker = useRef(null)
  const locateGraphNode = useRef(null)
  const [graph, setGraph] = useState(false)
  const [graphFolder, setGraphFolder] = useState('')
  const [graphTrail, setGraphTrail] = useState([])
  const overviewCamera = useRef(null)
  const graphFocus = graphTrail.at(-1)
  const paths = new Set()
  for (const path of collections) {
    for (let part = path; part; part = parentFolder(part)) paths.add(part)
  }
  const folders = [...paths].sort((a, b) => a.localeCompare(b, 'zh-CN'))
  const foldersByParent = new Map()
  for (const path of folders) {
    const parent = parentFolder(path)
    if (!foldersByParent.has(parent)) foldersByParent.set(parent, [])
    foldersByParent.get(parent).push(path)
  }
  const searching = !!query.trim()
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
    if (!graph && !searching) return
    let active = true
    setResult(null)
    setError('')
    const timer = setTimeout(
      () =>
        call(api, graph ? 'graph_view' : 'search', graph
          ? graphFocus ? { focus_id: graphFocus.id } : { collection: graphFolder || null }
          : { query, offset, grouped: true })
          .then((data) => {
            if (active) setResult(data)
          })
          .catch((e) => {
            if (active) setError(message(e))
          }),
      !graph && query ? 150 : 0
    )
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [api, query, offset, revision, graph, graphFolder, graphFocus, searching])
  useEffect(() => {
    if (detail.current) detail.current.scrollTop = 0
  }, [target?.id])
  function refresh() {
    setOffset(0)
    setRevision((n) => n + 1)
  }
  function openGraphDocument(node, camera) {
    if (graphFocus) {
      setGraphTrail((trail) => [...trail.slice(0, -1), { ...trail.at(-1), camera }])
    } else {
      overviewCamera.current = camera
    }
    revealFolder(node.collection ?? '')
    setQuery('')
    setOffset(0)
    setResult(null)
    setGraph(false)
    setTarget(node)
  }
  function exploreGraph(node, camera) {
    if (graphFocus?.id === node.id) return
    if (!graphFocus) overviewCamera.current = camera
    setResult(null)
    setGraphTrail((trail) => {
      const previous = trail.at(-1)
      const next = { id: node.id, title: node.title }
      if (previous?.id === node.id) return trail
      return previous ? [...trail.slice(0, -1), { ...previous, camera }, next] : [next]
    })
  }
  function leaveGraphFocus(toOverview) {
    setResult(null)
    setGraphTrail((trail) => toOverview ? [] : trail.slice(0, -1))
  }
  function documentChanged(saved) {
    if (saved && target?.id === saved.id) {
      setTarget((row) => row?.id === saved.id ? {
        ...row, revision: saved.revision, title: saved.title, collection: saved.collection,
      } : row)
      if (saved.collection !== collection) revealFolder(saved.collection)
    }
    refresh()
  }
  function openFolder(path) {
    revealFolder(path)
    setTarget(null)
    setQuery('')
    setOffset(0)
    setGraph(false)
  }
  function revealFolder(path) {
    setExpanded((value) => {
      const next = new Set(value)
      for (let part = path; part; part = parentFolder(part)) next.add(part)
      return next
    })
    setCollection(path)
  }
  async function moveSource(source, destination, undo = false) {
    if (moveInFlight.current || destination === source.collection) return
    moveInFlight.current = true
    setMovePending(true)
    try {
      const saved = await call(api, 'move', {
        id: source.id, revision: source.revision, from: source.collection, collection: destination,
      })
      documentChanged(saved)
      if (target?.id !== saved.id) revealFolder(destination)
      setMoveMessage({ text: undo ? '已撤销移动' : '已移到 ' + displayLocation(destination), undo: undo ? null : { source: saved, path: source.collection } })
    } finally {
      moveInFlight.current = false
      setMovePending(false)
    }
  }
  function moveFromBrowser(source, destination, undo = false) {
    moveSource(source, destination, undo).catch((error) => {
      setMoveMessage({ text: message(error), error: true })
      refresh()
    })
  }
  function finishDrag() {
    dragging.current = null
    setShowTopDrop(false)
    setDropTarget(null)
  }
  function folderDrop(path) {
    return {
      'data-drop-target': dropTarget === path || undefined,
      onDragOver(event) {
        if (!dragging.current || dragging.current.collection === path || moveInFlight.current) return
        event.preventDefault()
        event.dataTransfer.dropEffect = 'move'
        setDropTarget(path)
      },
      onDragLeave() { setDropTarget((value) => value === path ? null : value) },
      onDrop(event) {
        const source = dragging.current
        if (!source) return
        event.preventDefault()
        finishDrag()
        moveFromBrowser(source, path)
      },
    }
  }
  function selectDocument(row) {
    setCollection(row.collection ?? '')
    setTarget(row)
  }
  function documentRow(row) {
    return (
      <li key={row.id} className="alcor-library-row alcor-library-file-row">
        <button type="button" className="alcor-library-entry" draggable={!movePending}
          aria-current={target?.id === row.id ? 'page' : undefined}
          aria-label={labels[row.kind] + ' ' + row.title} title={row.title}
          onClick={() => selectDocument(row)}
          onDragStart={(event) => {
            dragging.current = row
            setShowTopDrop(!!row.collection)
            event.dataTransfer.effectAllowed = 'move'
            event.dataTransfer.setData('application/x-alcor-knowledge-document', row.id)
          }}
          onDragEnd={finishDrag}>
          <IconDeliverDocRegular size={17} aria-hidden="true" />
          <span><strong>{row.title}</strong>{searching && <small>{displayLocation(row.collection)}{row.kind === 'pdf' ? ' · 第 ' + row.page + ' 页' : ''}</small>}</span>
        </button>
        <div className="alcor-library-row-tools">
          <LibraryIconButton icon={IconEditOutlineRegular} label={`重命名文件：${row.title}`}
            title="重命名文件" onClick={() => setNaming(row)} />
          <LibraryIconButton icon={IconTrashOutlineRegular} label={`删除文件：${row.title}`}
            title="删除文件" onClick={() => setDeleting(row)} />
        </div>
      </li>
    )
  }
  function renderFolder(path) {
    const isOpen = expanded.has(path)
    const name = folderName(path)
    return (
      <li key={path}>
        <div className="alcor-library-row alcor-library-folder-row" {...folderDrop(path)}>
          <button type="button" className="alcor-library-entry" title={displayLocation(path)}
            aria-expanded={isOpen} aria-current={!target && collection === path ? 'location' : undefined}
            onClick={() => {
              setCollection(path)
              setTarget(null)
              setExpanded((value) => {
                const next = new Set(value)
                if (next.has(path)) next.delete(path)
                else next.add(path)
                return next
              })
            }}>
            {isOpen ? <IconChevronDownOutlineRegular size={12} aria-hidden="true" /> : <IconChevronRightOutlineRegular size={12} aria-hidden="true" />}
            <IconFolderOpenRegular size={17} aria-hidden="true" />
            <span>{name}</span>
          </button>
          <LibraryActions className="alcor-library-row-tools" name={name}
            onCreateFile={() => setEditor({ initialCollection: path })}
            onCreateFolder={() => setNaming({ parent: path })}
            onRename={() => setNaming({ path })}
            onDelete={() => setDeleting({ path })} />
        </div>
        {isOpen && <ul className="alcor-library-tree">
          {(foldersByParent.get(path) ?? []).map(renderFolder)}
          <FolderDocuments api={api} path={path} revision={revision} renderDocument={documentRow} onRetry={refresh} />
        </ul>}
      </li>
    )
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
                    setTarget(null)
                    setGraph(value)
                  }
                }}
              >
                {label}
              </button>
            ))}
          </div>
          <Button type="button" variant="primary" onClick={() => picker.current.click()}>
            <IconPlusOutlineRegular size={16} aria-hidden="true" />
            导入文件
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
      {moveMessage && (
        <Notice className="alcor-library-feedback" tone={moveMessage.error ? 'error' : undefined} role={moveMessage.error ? 'alert' : 'status'}>
          <span>{moveMessage.text}</span>
          {moveMessage.undo && <Button size="sm" variant="ghost" disabled={movePending}
            onClick={() => moveFromBrowser(moveMessage.undo.source, moveMessage.undo.path, true)}>撤销</Button>}
          <Button size="sm" variant="ghost" aria-label="关闭提示" onClick={() => setMoveMessage(null)}>
            <IconCloseOutlineRegular size={14} aria-hidden="true" />
          </Button>
        </Notice>
      )}
      {graph ? (
        <div className="alcor-knowledge-graph-view">
          <div className="alcor-knowledge-toolbar alcor-graph-filters">
            {graphFocus ? <>
              {graphTrail.length > 1 && <Button variant="outline" onClick={() => leaveGraphFocus(false)}>返回上一步</Button>}
              <Button variant="outline" onClick={() => leaveGraphFocus(true)}>返回全局图谱</Button>
              <span title={graphFocus.title}>局部图谱 · {graphFocus.title}</span>
            </> : <>
              <GraphTitleFinder key={graphFolder} api={api} collection={graphFolder} revision={revision}
                disabled={!result} onLocate={(row) => locateGraphNode.current?.(row)} />
              {folders.length > 0 && <select aria-label="筛选图谱文件夹" title={graphFolder ? displayLocation(graphFolder) : '全部文件夹'} value={graphFolder}
                onChange={(event) => {
                  overviewCamera.current = null
                  setResult(null)
                  setGraphFolder(event.target.value)
                }}>
                <option value="">全部文件夹</option>
                {folders.map((path) => <option key={path} value={path}>{displayLocation(path)}</option>)}
              </select>}
            </>}
          </div>
          {error ? <Notice tone="error" role="alert">{error} <Button onClick={refresh}>重试</Button></Notice>
            : result ? <KnowledgeGraph data={result} onOpen={openGraphDocument} onFocus={exploreGraph}
              focusId={graphFocus?.id} locateRef={locateGraphNode}
              camera={graphFocus ? graphFocus.camera : overviewCamera.current} />
              : <p role="status">正在读取资料…</p>}
        </div>
      ) : (
        <div className="alcor-library-split" data-reading={target ? '' : undefined}>
          <nav aria-label="知识库目录" className="alcor-library-sidebar">
            <div className="alcor-library-directory-bar">
              <div className="alcor-knowledge-toolbar">
                <input type="search" aria-label="搜索知识库" placeholder="搜索资料" maxLength={240} value={query}
                  onChange={(event) => { setQuery(event.target.value); setResult(null); setOffset(0) }} />
              </div>
              <LibraryActions className="alcor-library-directory-tools"
                onCreateFile={() => setEditor({ initialCollection: collection })}
                onCreateFolder={() => setNaming({ parent: collection })} />
              {showTopDrop && <div className="alcor-library-top-drop" {...folderDrop('')}>移到顶层</div>}
            </div>
            <div className="alcor-library-scroll">
              {searching ? <>
                {error ? <Notice tone="error" role="alert">{error} <Button onClick={refresh}>重试</Button></Notice>
                  : !result ? <p role="status">正在读取资料…</p>
                    : !result.items.length ? <p role="status">没有找到匹配资料</p>
                      : <ul className="alcor-library-tree">{result.items.map(documentRow)}</ul>}
                {(offset > 0 || result?.next_offset != null) && <div className="alcor-library-pagination">
                  <Button size="sm" disabled={!offset} onClick={() => setOffset((n) => Math.max(0, n - 25))}>上一批</Button>
                  <Button size="sm" disabled={result?.next_offset == null} onClick={() => setOffset(result.next_offset)}>下一批</Button>
                </div>}
              </> : (
                <ul className="alcor-library-tree">
                  {(foldersByParent.get('') ?? []).map(renderFolder)}
                  <FolderDocuments api={api} path="" revision={revision} renderDocument={documentRow} onRetry={refresh} />
                </ul>
              )}
            </div>
          </nav>
          <section className="alcor-library-detail" aria-label="知识库阅读区" ref={detail}>
            {target ? <>
              <Button className="alcor-library-back" size="sm" variant="ghost" onClick={() => setTarget(null)}>返回目录</Button>
              <SourceReader key={target.id + ':' + (target.page ?? 1)} target={target} api={api} inline
                collections={folders} close={() => setTarget(null)} onChange={documentChanged}
                libraryRevision={revision} />
            </> : <Empty title={collection ? folderName(collection) : '选择资料'} icon={<IconDeliverDocRegular size={28} />} />}
          </section>
        </div>
      )}
      {deleting && <DeleteLibraryEntry api={api} entry={deleting} onClose={() => setDeleting(null)}
        onDeleted={() => {
          setDeleting(null)
          if (deleting.path) {
            if (inFolder(collection, deleting.path)) setCollection(parentFolder(deleting.path))
            if (target && inFolder(target.collection ?? '', deleting.path)) setTarget(null)
            setExpanded((value) => new Set([...value].filter((path) => !inFolder(path, deleting.path))))
          } else if (deleting.id === target?.id) {
            setTarget(null)
          }
          refresh()
        }} />}
      {naming && <LibraryNameDialog api={api} entry={naming} folders={folders} onClose={() => setNaming(null)}
        onSaved={(saved) => {
          setNaming(null)
          if (naming.id) {
            setGraphTrail((trail) => trail.map((step) => step.id === saved.id ? { ...step, title: saved.title } : step))
            setMoveMessage(null)
            documentChanged(saved)
            return
          }
          if (naming.path) {
            const remap = (path) => inFolder(path, naming.path) ? saved.path + path.slice(naming.path.length) : path
            setExpanded((value) => new Set([...value].map(remap)))
            setCollection(remap)
            setGraphFolder(remap)
            setTarget((row) => row ? { ...row, collection: remap(row.collection ?? '') } : row)
            setMoveMessage(null)
          } else {
            openFolder(saved.path)
          }
          refresh()
        }} />}
      {editor && (
        <Editor
          {...editor}
          initialCollection={editor.initialCollection ?? (graph ? '' : collection)}
          collections={folders}
          api={api}
          onClose={() => setEditor(null)}
          onSaved={(path) => {
            setEditor(null)
            openFolder(path)
            refresh()
          }}
        />
      )}
    </main>
  )
}
