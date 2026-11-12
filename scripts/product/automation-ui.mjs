import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

// Retain our MIT-derived manager as Alcor UI; never apply its official-module registration.
export function automationUiPlugin() {
  return { name: 'alcor-automation-ui', setup(build) {
    build.onResolve({ filter: /^alcor:automation-ui$/ }, () => ({ path: 'manager', namespace: 'alcor-automation-ui' }))
    build.onLoad({ filter: /.*/, namespace: 'alcor-automation-ui' }, async () => {
      const directory = new URL('../../packages/alcor-secretary/vendor/', import.meta.url)
      const source = await readFile(new URL('ui-schedule/lib/client.js', directory), 'utf8')
      const provenance = JSON.parse(await readFile(new URL('NATIVE-UI-PROVENANCE.json', directory), 'utf8'))
      if (createHash('sha256').update(source).digest('hex') !== provenance.clients['ui-schedule'].client) throw Error('Retained automation UI provenance mismatch')
      const start = source.indexOf('\t\tObject.defineProperty(exports,'), end = source.lastIndexOf('\t\texports.apply = apply;')
      if (start < 0 || end < start || source.split('props.renderSlot("schedule.manager.header", {})').length !== 2) throw Error('Unexpected retained automation UI structure')
      let contents = source.slice(start, end).replace('props.renderSlot("schedule.manager.header", {})', 'props.managerHeader ?? props.renderSlot("alcor.automation.header", {})') + `
exports.registerAutomationUi = function(ctx) {
  ctx.effect(() => ctx.locale.register('alcor.automation', {zh,en,ja,es,fr,de,ru,pt}));
  ctx.effect(()=>ctx.slots.registerFactory({name:'alcor.automation.view',scope:'root',locale:'alcor.automation',
    children:{'alcor.automation.header':{kind:'list',scope:'root'}}, store:createEditorStore(),
    inject:()=>({wire:(remote,onNew)=>wireManager(ctx,remote,()=>{},()=>{},id=>ctx.uiWorkspace.openSession(id),onNew)})
  },ScheduleManager),'alcor: automation view');
};
`
      contents = "const {useAutomationAnchor} = require('./packages/alcor-secretary/src/automation-anchor.jsx');\n" + contents.replaceAll('_deepseek_ai_dsh_client_ui_primitives.useAnchoredPosition', 'useAutomationAnchor')
      // Keep the retained view's CSS separate from the independently updated native view.
      contents = contents.replaceAll('@deepseek-ai/dsh-client-ui-schedule/', '@alcor/automation-ui/')
      for (const prefix of new Set([...source.matchAll(/\.([A-Za-z0-9]{6}_)\w/g)].map(match=>match[1]))) contents = contents.replaceAll(prefix, 'alcor_' + prefix)
      return { contents, loader: 'js', resolveDir: fileURLToPath(new URL('../../', import.meta.url)) }
    })
  } }
}
