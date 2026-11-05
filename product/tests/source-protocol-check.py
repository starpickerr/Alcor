"""Probe installed frozen servers without fetching personal or network data."""
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    for package, binary, env_key, value, names in [
        ('elearning', 'elearning-mcp', 'STELLA_ELEARNING_CREDENTIAL', {'username': 'synthetic', 'password': 'synthetic-school-secret'}, {'list_courses', 'fetch', 'list_files'}),
        ('mail', 'email-mcp', 'STELLA_EMAIL_CREDENTIAL', {'accounts': [{'id': 'synthetic', 'host': 'imap.example.com', 'user': 'test@example.com', 'password': 'synthetic-mail-secret', 'port': 993, 'folder': 'INBOX', 'enabled': True}]}, {'list_accounts', 'list_sources', 'get_cursors', 'fetch_messages', 'test_connection', 'list_folders'}),
    ]:
        root = Path(sys.argv[1]) / f'@stella/{package}/server'
        assert not any(path.is_symlink() for path in root.rglob('*'))
        with tempfile.TemporaryDirectory(prefix='source-protocol-') as data:
            parameters = StdioServerParameters(command=str(root / binary), args=['--data-dir', data, '--dsh-credentials'], cwd=data,
                env={'HOME': data, env_key: json.dumps(value)})
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    result = await asyncio.wait_for(session.initialize(), 20)
                    assert result.serverInfo.version == '1.0.5'
                    tools = await session.list_tools()
                    assert {row.name for row in tools.tools} == names
                    assert all(row.annotations.readOnlyHint for row in tools.tools)
                    if package == 'mail':
                        sources = await session.call_tool('list_sources', {})
                        assert sources.structuredContent['value'][0]['mailbox_id'] == 'synthetic'
                    rejected = await session.call_tool('save_account', {'payload': {'password': 'never-write'}})
                    assert rejected.isError
            assert not any(b'synthetic-mail-secret' in path.read_bytes() or b'synthetic-school-secret' in path.read_bytes() for path in Path(data).rglob('*') if path.is_file())
    print('PASS: installed frozen source servers run outside development paths; exact readonly tools and versions; no plaintext business secrets')


if __name__ == '__main__': asyncio.run(main())
