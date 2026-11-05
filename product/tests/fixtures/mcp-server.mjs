import { createInterface } from 'node:readline'

for await (const line of createInterface({ input: process.stdin })) {
  const request = JSON.parse(line)
  if (request.id === undefined) continue
  let result
  if (request.method === 'initialize') result = { protocolVersion: request.params.protocolVersion, capabilities: { tools: {} }, serverInfo: { name: 'synthetic-elearning', version: '1.0' } }
  else if (request.method === 'tools/list') result = { tools: ['list_courses', 'save_settings', 'fetch'].map(name => ({ name, description: 'Synthetic MCP probe', inputSchema: { type: 'object', properties: {}, additionalProperties: false } })) }
  else if (request.method === 'tools/call' && request.params.name === 'fetch') result = { isError: true, content: [{ type: 'text', text: 'Content not available in metadata fixture' }] }
  else if (request.method === 'tools/call') result = { content: [{ type: 'text', text: 'synthetic-course' }], structuredContent: { value: [{ id: 'synthetic-course', name: 'Synthetic course' }] } }
  else result = {}
  process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: request.id, result }) + '\n')
}
