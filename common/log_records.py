"""Bounded exploration of existing logs. No database, arbitrary paths or writes."""
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections import Counter
from datetime import datetime
import json
import os
from pathlib import Path
import re

from common.log import _redact

MAX_SCAN_BYTES = 4 * 1024 * 1024
MAX_RECORD_BYTES = 16 * 1024
LEVELS = {'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL', 'UNKNOWN'}
_LINE = re.compile(r'^\[(DEBUG|INFO|WARNING|ERROR|CRITICAL)\]\[([^]]+)\]\[([^]]+)\] - (.*)$')
_SHELL = re.compile(r'^\[SHELL\]\[([^]]+)\]\s*(.*)$')
_TAG = re.compile(r'^\[(Plugin|MCP):([^]]+)\]')
_DOMAINS = {'briefing': ('briefing',), 'tasks': ('todos', 'todo', 'tasks'), 'calendar': ('calendar', 'timezone'),
            'email': ('email', 'imap', 'mail'), 'elearning': ('elearning',), 'scheduler': ('scheduler', 'notification'),
            'knowledge': ('knowledge',), 'memory': ('memory', 'deepdream'), 'agent': ('agent', 'tool', 'subagent'),
            'data': ('data',), 'security': ('security', 'permission', 'authorization'),
            'bills': ('bill', 'ledger'), 'models': ('model', 'llm', 'openai', 'anthropic'),
            'channels': ('channel', 'feishu', 'telegram', 'wechat', 'dingtalk')}


def _instant(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone().timestamp()


def _source(message, location):
    tag = _TAG.match(message)
    component = tag[1].lower() if tag else 'core'
    name = tag[2].lower().removeprefix('stella.') if tag else (message.split(']', 1)[0] + ' ' + location).lower()
    if message.startswith('[DesktopChannel]'):
        name = message.partition(']')[2].strip().split(' ', 1)[0].lower() + ' ' + name
    for source, prefixes in _DOMAINS.items():
        if any(prefix in name for prefix in prefixes):
            return source, component
    return (name[:80] if tag else 'core'), component


def _parse(text):
    text = _redact(text)
    first = text.split('\n', 1)[0]
    match, shell = _LINE.match(first), _SHELL.match(first)
    if match:
        level, timestamp, location, message = match.groups()
        source, component = _source(message, location)
    elif shell:
        timestamp, message = shell.groups()
        level, location, source, component = 'INFO', '', 'shell', 'desktop'
    else:
        try:
            row = json.loads(first)
        except (ValueError, TypeError):
            row = None
        if isinstance(row, dict) and isinstance(row.get('message'), str) and isinstance(row.get('level'), str):
            level = row['level'].upper()
            timestamp, location, message = str(row.get('timestamp') or ''), str(row.get('location') or ''), row['message']
            source, component = str(row.get('source') or 'core')[:80], str(row.get('component') or 'core')[:40]
            text = message + ('\n' + str(row['detail']) if row.get('detail') else '')
        else:
            level, timestamp, location, message = 'UNKNOWN', '', '', first
            source, component = _source(message, location)
    return dict(timestamp=timestamp, level=level if level in LEVELS else 'UNKNOWN', source=source,
                component=component, location=location, message=message[:MAX_RECORD_BYTES], detail=text[:MAX_RECORD_BYTES])


def _cursor(identity, offset):
    return urlsafe_b64encode(json.dumps([*identity, offset], separators=(',', ':')).encode()).decode().rstrip('=')


def _decode_cursor(cursor):
    if not cursor:
        return None
    try:
        if len(cursor) > 200:
            raise ValueError()
        values = json.loads(urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
        if not isinstance(values, list) or len(values) != 3 or any(type(v) is not int or v < 0 for v in values):
            raise ValueError()
        return values
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError('Invalid log history cursor') from exc


def _records(data, start):
    """Join traceback/indented continuations without losing byte positions."""
    records = []
    offset = start
    for raw in data.splitlines(keepends=True):
        if not raw.endswith(b'\n'):
            break  # A writer may still be completing this line.
        text = raw.decode('utf-8', errors='replace').rstrip('\r\n')
        continuation = (text.startswith((' ', '\t', 'Traceback ', 'During handling ', 'The above exception'))
                        or bool(re.match(r'^[\w.]+(?:Error|Exception):', text)) or not text)
        if continuation and records:
            if len(records[-1][1]) < MAX_RECORD_BYTES:
                records[-1][1] += '\n' + text[:MAX_RECORD_BYTES]
        else:
            # JSON may escape each Unicode character into six ASCII bytes.
            # Parse the bounded envelope before truncating its decoded fields.
            records.append([offset, text[:MAX_RECORD_BYTES * 8] if text.startswith('{') else text[:MAX_RECORD_BYTES]])
        offset += len(raw)
    return records


def query_logs(root, *, query='', levels='', source='', since='', cursor='', limit=100):
    if type(limit) is not int or not 1 <= limit <= 200 or len(query) > 500 or len(source) > 80:
        raise ValueError('Invalid log query')
    wanted = set(filter(None, levels.upper().split(',')))
    if wanted - LEVELS:
        raise ValueError('Invalid log level')
    try:
        after = _instant(since) if since else None
    except (ValueError, TypeError) as exc:
        raise ValueError('Invalid log time range') from exc
    before = _decode_cursor(cursor)
    found_cursor = before is None
    remaining = MAX_SCAN_BYTES
    entries, sources, scanned = [], Counter(), 0
    last_cursor, truncated = None, False
    seen = set()
    for suffix in ('', '.1', '.2', '.3'):
        path = Path(root) / ('run.log' + suffix)
        try:
            # Log files are a fixed allowlist. Never follow symlinks into data.
            flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
            if path.is_symlink():
                continue
            with os.fdopen(os.open(path, flags), 'rb') as stream:
                stat = os.fstat(stream.fileno())
                identity = [stat.st_dev, stat.st_ino]
                if tuple(identity) in seen:
                    continue
                seen.add(tuple(identity))
                if not found_cursor:
                    if identity != before[:2]:
                        continue
                    found_cursor = True
                    if before[2] > stat.st_size:
                        raise ValueError('Log history changed; refresh the view')
                end = before[2] if before and identity == before[:2] else stat.st_size
                start = max(0, end - remaining)
                if start:
                    stream.seek(start - 1)
                    partial = stream.read(1) != b'\n'
                else:
                    partial = False
                stream.seek(start)
                data = stream.read(end - start)
                scanned += len(data)
                remaining -= len(data)
                if partial:
                    skip = data.find(b'\n') + 1
                    data, start = data[skip:], start + skip
                records = _records(data, start)
        except (FileNotFoundError, PermissionError, IsADirectoryError):
            continue
        for offset, text in reversed(records):
            last_cursor = _cursor(identity, offset)
            row = _parse(text)
            if wanted and row['level'] not in wanted:
                continue
            if after is not None:
                try:
                    if not row['timestamp'] or _instant(row['timestamp']) < after:
                        continue
                except ValueError:
                    continue
            if query.casefold() not in (row['detail'] + ' ' + row['source']).casefold():
                continue
            sources[row['source']] += 1
            if source and row['source'] != source:
                continue
            row['id'] = last_cursor
            entries.append(row)
            if len(entries) > limit:
                return dict(entries=entries[:limit], next_cursor=entries[limit - 1]['id'], truncated=False,
                            sources=dict(sources), scanned_bytes=scanned)
        if remaining <= 0:
            truncated = True
            break
    if not found_cursor:
        raise ValueError('Log history expired; refresh the view')
    return dict(entries=entries, next_cursor=last_cursor if truncated else None, truncated=truncated,
                sources=dict(sources), scanned_bytes=scanned)
