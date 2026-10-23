import React, { useEffect, useMemo, useState } from 'react'
import { apiClient } from '../../api/client'
import { getLang, t } from '../../i18n'
import { PluginRow, SettingsDescriptor, SettingsSnapshot } from '../../plugins/managementTypes'
import { Dropdown, Group, Row, SaveRow, Segmented, TextInput, Toggle } from './primitives'
import { hasPluginDomainPanel, PluginDomainPanel } from './PluginDomainPanels'

type Draft = { snapshot: SettingsSnapshot; values: Record<string, unknown>; conflict?: boolean }
type Props = {
  baseUrl: string
  embedded?: boolean
  pluginId?: string | null
  onPluginChange?: (pluginId: string) => void
}

/** Same shell as General / Assistant: one column of Group cards, no nested sidebar. */
export default function PluginSettings({ baseUrl, pluginId, onPluginChange, embedded = false }: Props) {
  const [rows, setRows] = useState<PluginRow[]>([])
  const [selected, setSelected] = useState('')
  const [descriptors, setDescriptors] = useState<SettingsDescriptor[]>([])
  const [section, setSection] = useState('domain')
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

  const row = rows.find((item) => item.pluginId === selected)
  const available = row?.intent === 'enabled' && !['maintenance', 'failed'].includes(row.state)
  const categories = descriptors.flatMap((descriptor) =>
    descriptor.categories.map((category) => ({ ...category, descriptor })),
  )
  const category = categories.find((item) => `${item.descriptor.id}/${item.id}` === section)
  const draftKey = `${selected}/${section}`
  const draft = drafts[draftKey]
  const fields = category?.descriptor.fields.filter((field) => field.category === category.id) || []
  const showDomain = !!selected && hasPluginDomainPanel(selected)

  const sectionOptions = useMemo(() => {
    const options: { value: string; label: string }[] = []
    if (showDomain) options.push({ value: 'domain', label: t('settings_plugin_domain_tab') })
    for (const item of categories) {
      options.push({ value: `${item.descriptor.id}/${item.id}`, label: item.title })
    }
    return options
  }, [showDomain, categories])

  const selectPlugin = (next: string) => {
    setSelected(next)
    onPluginChange?.(next)
  }

  useEffect(() => {
    apiClient.setBaseUrl(baseUrl)
    let cancelled = false
    void apiClient
      .pluginRequest<{ plugins: PluginRow[] }>('runtime')
      .then((data) => {
        if (cancelled) return
        if (!Array.isArray(data.plugins)) throw new Error(getLang() === 'zh' ? '无法读取插件目录，请重试。' : 'Could not read the plugin inventory. Retry.')
        setRows(data.plugins)
        setSelected((value) => {
          if (pluginId && data.plugins.some((item) => item.pluginId === pluginId)) return pluginId
          return value || data.plugins[0]?.pluginId || ''
        })
      })
      .catch((err) => {
        if (!cancelled) setError(String(err))
      })
    return () => {
      cancelled = true
    }
  }, [baseUrl, pluginId])

  useEffect(() => {
    if (pluginId && pluginId !== selected && rows.some((item) => item.pluginId === pluginId)) {
      setSelected(pluginId)
    }
  }, [pluginId, rows, selected])

  useEffect(() => {
    if (!selected) return
    let cancelled = false
    setDescriptors([])
    setError('')
    setMessage('')
    setSection(hasPluginDomainPanel(selected) ? 'domain' : '')
    void apiClient
      .pluginRequest<SettingsDescriptor[]>('settings', { action: 'describe', pluginId: selected })
      .then((result) => {
        if (cancelled) return
        if (!Array.isArray(result) || result.some(item => !Array.isArray(item.categories) || !Array.isArray(item.fields))) {
          throw new Error(getLang() === 'zh' ? '无法读取插件配置，请重试。' : 'Could not read plugin settings. Retry.')
        }
        setDescriptors(result)
      })
      .catch((err) => {
        if (!cancelled) setError(String(err))
      })
    return () => {
      cancelled = true
    }
  }, [selected, available])

  useEffect(() => {
    if (sectionOptions.length && !sectionOptions.some(option => option.value === section)) setSection(sectionOptions[0].value)
  }, [sectionOptions, section])

  useEffect(() => {
    if (!category || draft) return
    let cancelled = false
    void apiClient
      .pluginRequest<SettingsSnapshot>('settings', {
        action: 'read',
        pluginId: selected,
        settingsId: category.descriptor.id,
        category: category.id,
      })
      .then((snapshot) => {
        if (!cancelled) {
          if (!snapshot || !Number.isInteger(snapshot.revision) || !snapshot.values || typeof snapshot.values !== 'object' || Array.isArray(snapshot.values)) {
            throw new Error(getLang() === 'zh' ? '插件配置响应无效，请重新读取。' : 'Invalid plugin settings response. Reload it.')
          }
          setDrafts((previous) =>
            previous[draftKey] ? previous : { ...previous, [draftKey]: { snapshot, values: snapshot.values } },
          )
        }
      })
      .catch((err) => {
        if (!cancelled) setError(String(err))
      })
    return () => {
      cancelled = true
    }
  }, [selected, category?.id, category?.descriptor.id, draft, draftKey])

  async function run(work: () => Promise<void>) {
    setBusy(true)
    setError('')
    setMessage('')
    try {
      await work()
    } catch (err) {
      setError(String(err))
    } finally {
      setBusy(false)
    }
  }

  async function save() {
    if (!category || !draft) return
    await run(async () => {
      const changes = Object.fromEntries(
        Object.entries(draft.values).filter(
          ([name, value]) => JSON.stringify(value) !== JSON.stringify(draft.snapshot.values[name]),
        ),
      )
      if (!Object.keys(changes).length) {
        setMessage(t('settings_plugin_no_changes'))
        return
      }
      try {
        const snapshot = await apiClient.pluginRequest<SettingsSnapshot>('settings', {
          action: 'save',
          pluginId: selected,
          settingsId: category.descriptor.id,
          category: category.id,
          expectedRevision: draft.snapshot.revision,
          changes,
        })
        setDrafts((previous) => ({ ...previous, [draftKey]: { snapshot, values: snapshot.values } }))
        setMessage(t('settings_plugin_saved'))
      } catch (err) {
        setDrafts((previous) => ({ ...previous, [draftKey]: { ...draft, conflict: true } }))
        throw new Error(`${String(err)}。${t('settings_plugin_draft_kept')}`)
      }
    })
  }

  async function mergeLatest() {
    if (!category || !draft) return
    await run(async () => {
      const snapshot = await apiClient.pluginRequest<SettingsSnapshot>('settings', {
        action: 'read',
        pluginId: selected,
        settingsId: category.descriptor.id,
        category: category.id,
      })
      const edits = Object.fromEntries(
        Object.entries(draft.values).filter(
          ([name, value]) => JSON.stringify(value) !== JSON.stringify(draft.snapshot.values[name]),
        ),
      )
      setDrafts((previous) => ({ ...previous, [draftKey]: { snapshot, values: { ...snapshot.values, ...edits } } }))
      setMessage(t('settings_plugin_merged'))
    })
  }

  const edit = (name: string, value: unknown) =>
    setDrafts((previous) => ({
      ...previous,
      [draftKey]: { ...previous[draftKey], values: { ...previous[draftKey].values, [name]: value } },
    }))

  const pluginOptions = rows.map((item) => ({
    value: item.pluginId,
    label: item.name,
    hint: item.version || item.pluginId,
  }))
  return (
    <div className="space-y-8">
      {!embedded && <Group title={t('settings_plugin_picker_label')} description={getLang() === 'zh' ? '管理插件功能与配置。Agent 调用遵循当前对话或任务的权限模式。' : 'Configure plugin behavior. Agent operations follow the current conversation or task permission mode.'}>
        {!rows.length ? (
          <Row label={t('settings_plugin_empty')} />
        ) : (
          <Row
            label={t('settings_plugin_current')}
            hint={row ? `${row.pluginId}${row.version ? ` · ${row.version}` : ''}` : undefined}
            control={
              <div className="w-56 max-w-full">
                <Dropdown
                  value={selected}
                  options={pluginOptions}
                  disabled={busy}
                  onChange={selectPlugin}
                />
              </div>
            }
          />
        )}
      </Group>}

      {selected && sectionOptions.length > 1 && (
        <div className="px-1">
          <Segmented
            label={t('settings_plugin_section_label')}
            value={sectionOptions.some((option) => option.value === section) ? section : sectionOptions[0].value}
            options={sectionOptions}
            onChange={setSection}
          />
        </div>
      )}

      {error && (
        <p role="alert" className="rounded-card border border-danger-border bg-danger-soft px-4 py-3 text-sm text-danger">
          {error}
        </p>
      )}

      {section === 'domain' && showDomain && (
        <div className="space-y-8">
          <PluginDomainPanel pluginId={selected} />
        </div>
      )}

      {category && (
        <Group title={category.title} description={showDomain ? t('settings_plugin_prefs_hint') : undefined}>
          {!fields.length && <Row label={t('settings_plugin_empty_category')} />}
          {fields.map((field) => {
            if (field.secret) {
              return <Row key={field.key} label={field.title} hint={t('settings_plugin_secret_hint')} />
            }
            if (field.type === 'boolean') {
              return (
                <Row
                  key={field.key}
                  label={field.title}
                  hint={field.description}
                  control={
                    <Toggle
                      label={field.title}
                      disabled={!draft || busy}
                      checked={!!draft?.values[field.key]}
                      onChange={(checked) => edit(field.key, checked)}
                    />
                  }
                />
              )
            }
            if (field.type === 'enum' || (field.type === 'string' && !!field.validation?.enum?.length)) {
              return (
                <Row
                  key={field.key}
                  label={field.title}
                  hint={field.description}
                  control={
                    <div className="w-56 max-w-full">
                      <Dropdown
                        value={String(draft?.values[field.key] ?? '')}
                        disabled={!draft || busy}
                        options={(field.validation?.enum || []).map((value) => ({
                          value: String(value),
                          label: field.enumLabels?.[String(value)] ?? String(value),
                        }))}
                        onChange={(value) =>
                          edit(
                            field.key,
                            field.validation?.enum?.find((item) => String(item) === value),
                          )
                        }
                      />
                    </div>
                  }
                />
              )
            }
            return (
              <Row key={field.key} label={field.title} htmlFor={`plugin-field-${field.key}`}>
                <TextInput
                  id={`plugin-field-${field.key}`}
                  placeholder={field.placeholder}
                  disabled={!draft || busy}
                  type={['integer', 'number'].includes(field.type) ? 'number' : 'text'}
                  value={String(draft?.values[field.key] ?? '')}
                  onChange={(e) =>
                    edit(
                      field.key,
                      ['integer', 'number'].includes(field.type) ? Number(e.target.value) : e.target.value,
                    )
                  }
                />
              </Row>
            )
          })}
          {!!fields.filter((field) => !field.secret).length && (
            <div className="px-4 py-3.5 space-y-3">
              <SaveRow status={message} onSave={() => void save()} label={t('settings_plugin_save_category')} />
              {draft?.conflict && (
                <div className="flex justify-end">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void mergeLatest()}
                    className="h-8 px-3 rounded-btn border border-strong text-sm text-content-secondary hover:bg-surface-2 cursor-pointer disabled:opacity-50"
                  >
                    {t('settings_plugin_merge_latest')}
                  </button>
                </div>
              )}
            </div>
          )}
        </Group>
      )}

    </div>
  )
}
