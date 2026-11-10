// Deterministic adaptation of the MIT UI shipped inside the Alcor plugin.
// The official desktop application is never patched.
const hook = 'window[Symbol.for("@alcor/secretary.workspace-navigation")]'
const changes = [
  ['await workspaces.rename(workspaceId, title);', `if (${hook}) await ${hook}.renameWorkspace(workspaceId,title); else await workspaces.rename(workspaceId, title);`],
  ['await workspaces.delete(workspaceId);', `if (${hook}) await ${hook}.deleteWorkspace(workspaceId); else await workspaces.delete(workspaceId);`],
  ['function WorkspaceBrowser({ wide, usePanelInfo, expandSidebar, useSessions,', 'function WorkspaceBrowser({ wide, renderQQ, usePanelInfo, expandSidebar, useSessions: useAllSessions, useSpaceSessions,'],
  ['useSessionAccess, useWorkspaceAccess, useWorkspaces, useStore, actions', 'useSessionAccess, useWorkspaceAccess, useWorkspaces: useAllWorkspaces, useSpaceWorkspaces, useStore, actions'],
  ['const home = useHostInfo((info) => info.home);', 'const useSessions = useSpaceSessions ?? useAllSessions;\n\t\t\tconst useWorkspaces = useSpaceWorkspaces ?? useAllWorkspaces;\n\t\t\tconst home = useHostInfo((info) => info.home);'],
  ['function SessionTree({ list,', 'function SessionTree({ renderQQ, list,'],
  ['function FlatList({ list,', 'function FlatList({ renderQQ, list,'],
  ['(SessionTree, {\n\t\t\t\t\t\t\tuseRemoteAccess,', '(SessionTree, {\n\t\t\t\t\t\t\trenderQQ, useRemoteAccess,'],
  ['(FlatList, {\n\t\t\t\t\t\t\tuseSessionAccess,', '(FlatList, {\n\t\t\t\t\t\t\trenderQQ, useSessionAccess,'],
  ['children: [groups.length === 0 &&', 'children: [renderQQ?.(Rows_module_css_default), groups.length === 0 &&'],
  ['children: [rows.length === 0 &&', 'children: [renderQQ?.(Rows_module_css_default), rows.length === 0 &&'],
  ['className: WorkspaceBrowser_module_css_default.listArea,\n\t\t\t\t\t\tchildren: wide &&', 'className: WorkspaceBrowser_module_css_default.listArea,\n\t\t\t\t\t\tchildren: !wide ? renderQQ?.(Rows_module_css_default, expandSidebar) :'],
  ['function WorkspacePicker({ open, anchorRef, useWorkspaces,', 'function WorkspacePicker({ open, anchorRef, useWorkspaces: useAllWorkspaces, useSpaceWorkspaces,'],
  ['\n\t\t\t\tuseWorkspaces,\n\t\t\t\tcreateWorkspace,\n\t\t\t\tuseDirectoryFlow,\n\t\t\t\trenderDirectoryFlow', '\n\t\t\t\tuseWorkspaces: useSpaceWorkspaces ?? useAllWorkspaces,\n\t\t\t\tcreateWorkspace,\n\t\t\t\tuseDirectoryFlow,\n\t\t\t\trenderDirectoryFlow'],
  ['startSession(workspaceId, options) {', `startSession(workspaceId, options) {\n\t\t\t\tif (${hook}) return ${hook}.startSession(workspaceId, options);`],
  ['replaceMain(target, signal, panel, beforeOpen) {', `replaceMain(target, signal, panel, beforeOpen) {
                if (typeof target === 'string' && (${hook}?.isChannelSession(target) || /^onebot-(group|private)-[1-9]\\d{0,19}(?:-|$)/.test(target))) {
                    signal.throwIfAborted();
                    if (${hook}) ${hook}.openQQ(target);
                    else {
                        const open = () => { if (!signal.aborted) ${hook}?.openQQ(target); };
                        window.addEventListener('alcor-navigation-ready', open, { once: true });
                        signal.addEventListener('abort', () => window.removeEventListener('alcor-navigation-ready', open), { once: true });
                    }
                    return;
                }
                ${hook}?.selectSpace(target);`],
  ['const searchSessions = async (query, signal) => {', `const searchSessions = async (query, signal) => {\n                if (${hook}) return ${hook}.search(query, signal);`],
  ['const browserInjected = () => ({', `const productSource = (name, fallback) => ({
                getSnapshot: () => ${hook}?.[name].getSnapshot() ?? fallback.getSnapshot(),
                subscribe(listener) {
                    let release;
                    const connect = () => { release?.(); release = ${hook}?.[name].subscribe(listener); listener(); };
                    const stop = fallback.subscribe(listener);
                    window.addEventListener('alcor-navigation-ready', connect);
                    connect();
                    return () => { stop(); release?.(); window.removeEventListener('alcor-navigation-ready', connect); };
                }
            });
            const productSessions = productSource('sessions', sessions.list);
            const productWorkspaces = productSource('workspaces', workspaces.list);
            const productPicker = productSource('picker', workspaces.list);
            const createProductWorkspace = input => ${hook} ? ${hook}.createWorkspace(input) : workspaces.create(input);
            const browserInjected = () => ({`],
  ['createWorkspace: (input) => workspaces.create(input),\n\t\t\t\trequestSearch', 'createWorkspace: createProductWorkspace,\n\t\t\t\trequestSearch'],
  ['hooks: {\n\t\t\t\t\tworkspaceActionRequest', 'hooks: {\n\t\t\t\t\tspaceSessions: productSessions, spaceWorkspaces: productWorkspaces,\n\t\t\t\t\tworkspaceActionRequest'],
  ['createWorkspace: (input) => workspaces.create(input),\n\t\t\t\thooks: { directoryFlow: pickerFlowSource }', 'createWorkspace: createProductWorkspace,\n\t\t\t\thooks: { directoryFlow: pickerFlowSource, spaceWorkspaces: productPicker }'],
]
export function adaptWorkspaceClient(text, reverse = false) {
  for (const [from, to] of reverse ? [...changes].reverse() : changes) {
    const a = reverse ? to : from, b = reverse ? from : to
    if (a === b) continue
    if (text.split(a).length !== 2) throw Error('Unexpected native workspace source: ' + a.slice(0, 80))
    text = text.replace(a, b)
  }
  return text
}
