// Static classification only. A classification is not an authorization.
const table = {
  tasks: {
    read: ['catalog', 'get_todo', 'list_todos', 'list_todos_focus', 'list_uncertain_reminders', 'categories', 'get_category', 'find_briefing_sources', 'has_source_tombstone', 'scheduled_reminder_delivery', 'get_delivery'],
    resource: ['create_todo', 'update_todo', 'complete_todo', 'complete_checklist_item', 'change_todo_status', 'add_subtask', 'move_todo', 'add_dependency', 'remove_dependency', 'create_category', 'update_category', 'set_todo_category'],
    irreversible: ['delete_todo', 'delete_category', 'delete_external_todo'],
    internal: ['cancel_apple_operation', 'pending_scheduler_links', 'complete_scheduler_link', 'claim_due_todo_notifications', 'claim_desktop_reminders', 'acknowledge_desktop_reminder', 'retry_uncertain_reminder', 'queue_scheduled_reminder', 'queue_delivery', 'begin_delivery', 'finish_delivery', 'expire_delivery', 'ingest_briefing_revision', 'sync_briefing_facts', 'upsert_external_todo'],
  },
  calendar: {
    read: ['catalog', 'categories', 'get_category', 'get_event', 'find_briefing_sources', 'has_source_tombstone'],
    resource: ['list_events', 'create_category', 'update_category', 'set_event_category', 'create_event', 'update_event', 'import_china_holidays', 'create_special_date', 'import_special_dates'],
    irreversible: ['delete_category', 'delete_event', 'exclude_occurrence'],
    internal: ['cancel_apple_operation', 'upsert_external_event', 'ingest_briefing_revision', 'sync_briefing_facts'],
  },
  bills: {
    read: ['catalog', 'list_ledger', 'list_ledger_between', 'ledger_report', 'statement_reports', 'get_ledger', 'list_statements', 'get_statement', 'export_ledger', 'export_ledger_csv', 'get_operation'],
    local: ['create_ledger', 'update_ledger'],
    file: ['import_files'],
    irreversible: ['delete_ledger'],
  },
  briefing: {
    read: ['catalog', 'list_briefings', 'get_briefing', 'get_revision', 'briefing_for_date', 'list_rules', 'get_rule', 'get_cursor', 'list_action_rules', 'get_preferences', 'get_overview', 'get_action_targets', 'list_revisions', 'list_sources'],
    local: ['create_rule', 'update_rule', 'create_action_rule', 'update_preferences'],
    resource: ['update_action'],
    irreversible: ['delete_rule', 'delete_action_rule'],
    external: ['generate', 'resume_generation', 'cancel_generation', 'generation_status', 'available_sources'],
    internal: ['build_source_item', 'start_briefing', 'complete_briefing', 'run_pipeline', 'upsert_source_item', 'set_cursor', 'window_candidates', 'staged_window_candidates', 'select_sources', 'stage_source_items', 'stage_source_errors', 'dispatch_outbox', 'configure_sources', 'configure_source_groups', 'discover_sources', 'source_options'],
  },
  automation: {
    read: ['catalog', 'list', 'history'],
    schedule: ['create', 'run'],
    irreversible: ['deleteInactive'],
  },
  apple: {
    local: ['restore_local_item'],
    read: ['catalog', 'status', 'items', 'lookup_item', 'preview', 'preview_items', 'preview_batch'],
    apple: ['request_access', 'start_sync', 'configure', 'replace_links', 'disconnect', 'set_auto', 'one_time', 'sync', 'resolve_conflict', 'resolve_category_conflict', 'cancel_operation', 'sync_range', 'transfer_batch'],
  },
}
const methods = new Map(Object.entries(table).flatMap(([domain, categories]) => Object.entries(categories).flatMap(([kind, names]) => names.map(name => [`${domain}:${name}`, kind]))))

// Called with host-derived source kinds and session header, never tool arguments.
export function classifyActor(header, sources) {
  if (!header || !Array.isArray(sources) || !sources.length) return 'unknown'
  if (header.origin === 'subagent' || header.parentSession) return 'delegated'
  if (sources.every(source => source === 'user')) return 'user'
  if (sources.every(source => source === 'schedule')) return 'schedule'
  return 'unknown'
}

export function classifyCapability(name, args = {}) {
  if (typeof name !== 'string' || !name.startsWith('alcor_')) return { kind: 'not-alcor' }
  if (name === 'alcor_overview') return { kind: 'overview' }
  if (['alcor_import_bills', 'alcor_import_dates', 'alcor_export_bills'].includes(name)) return { kind: 'file' }
  if (name === 'alcor_generate_briefing') return { kind: 'briefing' }
  if (name === 'alcor_review_tasks') return { kind: 'read' }
  if (name !== 'alcor_business' || !args || typeof args !== 'object' || Array.isArray(args)) return { kind: 'unclassified' }
  if (typeof args.domain !== 'string' || typeof args.method !== 'string') return { kind: 'unclassified' }
  if (args.domain === 'automation' && args.method === 'edit') {
    if (args.arguments?.action === 'update') return { kind: 'schedule' }
    if (args.arguments?.action === 'delete') return { kind: 'irreversible' }
    return { kind: 'unclassified' }
  }
  const kind = methods.get(`${args.domain}:${args.method}`)
  return { kind: kind ?? 'unclassified' }
}

// Operations without target/version approval contracts remain unavailable.
// Exports and exact-bound item choices use the live Host plan gate, never raw invoke.
export function requiresUnimplementedAppleApproval(execution) {
  if (execution?.name !== 'alcor_business' || execution.arguments?.domain !== 'apple') return false
  const { method, arguments: args = {} } = execution.arguments
  if (!args || typeof args !== 'object' || Array.isArray(args)) return true
  if (method === 'resolve_category_conflict') return ['keep_alcor', 'keep_apple'].includes(args.choice)
  if (['start_sync', 'sync'].includes(method)) return ['confirm_deletions', 'confirm_local_deletions'].some(key => Object.hasOwn(args, key) && args[key] !== false)
  return false
}

// A host outcome (including allowed-once) does not attest a human confirmation.
export const permanentDeleteUnavailable = '缺少可验证的本次人工确认通道，不可恢复删除暂不可用。'
export function isUnverifiedPermanentDeletion(execution) {
  if (execution?.name !== 'alcor_business') return false
  const { domain, method, arguments: args = {} } = execution.arguments ?? {}
  if (domain === 'bills') return method === 'delete_ledger'
  return false
}
