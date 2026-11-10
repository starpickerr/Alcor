"""Count explicitly selected offline legacy assets without importing the old runtime."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import time
from contextlib import closing


DATABASES = {
    'user': ('todos/todos.db', 'calendar/calendar.db', 'briefing/briefing.db',
             'bills/bills.db', 'agent-hub/agent-hub.db', 'data/data.db', 'operations.db'),
    'agent': ('memory/long-term/index.db', 'scheduler/automation.db'),
    'shared': (),
}
TREES = {
    'user': ('plugins/memory', 'memory', 'output', 'work', 'runs', 'health',
             'plugins/automation-records'),
    'agent': (),
    'shared': ('knowledge',),
}
FILES = {
    'user': ('USER.md', 'MEMORY.md', '.migration.json'),
    'agent': ('scheduler/tasks.json',),
    'shared': ('.env', 'mcp.json'),
}
STATES = ('pending', 'running', 'completed', 'failed', 'outcome_unknown', 'unknown',
          'accepted', 'sending', 'waiting', 'expired', 'cancelled', 'canceled',
          'succeeded', 'draft', 'ready', 'applied', 'open', 'closed', 'paid', 'void')


class InventoryError(Exception):
    pass


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def checked(path):
    """Offline copies must not point back to a live source through links."""
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(info.st_mode):
            raise InventoryError('Snapshot contains a symbolic link; supply a self-contained offline copy.')
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise InventoryError('Snapshot contains an unsupported file type.')
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise InventoryError('Snapshot contains a hard link; supply independent copied files.')
    return info


def fingerprint(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def tree_summary(path):
    info = checked(path)
    if info is None:
        return {'present': False}
    if not stat.S_ISDIR(info.st_mode):
        raise InventoryError('An expected asset directory has the wrong type.')
    files = size = 0
    def fail(_error):
        raise InventoryError('An asset directory cannot be read.')
    for parent, directories, names in os.walk(path, followlinks=False, onerror=fail):
        for name in directories + names:
            entry = checked(Path(parent) / name)
            if entry is None:
                raise InventoryError('Snapshot changed while being inspected.')
            if stat.S_ISREG(entry.st_mode):
                files += 1
                size += entry.st_size
    return {'present': True, 'files': files, 'bytes': size}


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def database_summary(path):
    before = checked(path)
    if before is None:
        return {'present': False}
    if not stat.S_ISREG(before.st_mode):
        raise InventoryError('An expected database has the wrong type.')
    for suffix in ('-wal', '-shm', '-journal'):
        if checked(Path(str(path) + suffix)) is not None:
            raise InventoryError('Database sidecars are present; make a consistent offline SQLite backup first.')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA trusted_schema=OFF')
        deadline = time.monotonic() + 15
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        schema = db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
        tables = []
        for namespace, name, kind, *_ in sorted(db.execute('PRAGMA table_list').fetchall()):
            if namespace != 'main' or kind != 'table' or name.startswith('sqlite_'):
                continue
            table = {'table_sha256': digest(name), 'rows': db.execute('SELECT count(*) FROM ' + quote(name)).fetchone()[0]}
            columns = {row[1] for row in db.execute('PRAGMA table_info(' + quote(name) + ')')}
            for column in ('status', 'state'):
                if column not in columns:
                    continue
                field = quote(column)
                placeholders = ','.join('?' for _ in STATES)
                expression = f"CASE WHEN {field} IS NULL THEN '<null>' WHEN typeof({field})='text' AND {field} COLLATE BINARY IN ({placeholders}) THEN {field} ELSE '<other>' END"
                table[column + '_counts'] = dict(db.execute(f'SELECT {expression} AS bucket,count(*) FROM {quote(name)} GROUP BY 1', STATES))
            if name == 'todos' and 'completed_at' in columns:
                table['completion_counts'] = dict(db.execute("SELECT CASE WHEN completed_at IS NULL THEN 'open' ELSE 'completed' END AS bucket,count(*) FROM todos GROUP BY 1"))
            tables.append(table)
        result = {'present': True, 'bytes': before.st_size,
                  'schema_sha256': digest(json.dumps(schema, ensure_ascii=True, separators=(',', ':'))),
                  'tables': tables, 'schema_objects': len(schema),
                  'excluded_virtual_or_shadow_tables': sum(row[2] in ('virtual', 'shadow') for row in db.execute('PRAGMA table_list'))}
    after = checked(path)
    if after is None or fingerprint(before) != fingerprint(after):
        raise InventoryError('Snapshot changed while being inspected.')
    return result


def inventory(snapshot, selections, confirmed_offline=False):
    if not confirmed_offline:
        raise InventoryError('Explicit offline-copy confirmation is required.')
    snapshot = Path(os.path.abspath(snapshot))
    info = checked(snapshot)
    if info is None or not stat.S_ISDIR(info.st_mode):
        raise InventoryError('Snapshot root must be an existing independent directory.')
    if sqlite3.sqlite_version_info < (3, 37, 0):
        raise InventoryError('SQLite 3.37 or newer is required.')
    scopes = []
    seen = set()
    for role, relative in selections:
        if role not in DATABASES:
            raise InventoryError('Unknown inventory role.')
        relative = Path(relative)
        if relative.is_absolute() or '..' in relative.parts:
            raise InventoryError('Every selected root must stay inside the snapshot.')
        root = snapshot / relative
        info = checked(root)
        if info is None or not stat.S_ISDIR(info.st_mode):
            raise InventoryError('Every selected root must be an existing directory.')
        if (role, root) in seen:
            raise InventoryError('A scope was selected more than once.')
        seen.add((role, root))
        assets = {}
        for name in DATABASES[role]:
            assets[name] = database_summary(root / name)
        for name in TREES[role]:
            assets[name] = tree_summary(root / name)
        for name in FILES[role]:
            entry = checked(root / name)
            if entry is not None and not stat.S_ISREG(entry.st_mode):
                raise InventoryError('An expected asset file has the wrong type.')
            assets[name] = {'present': entry is not None}
        scopes.append({'role': role, 'root_sha256': digest(str(relative)), 'assets': assets})
    if not scopes:
        raise InventoryError('Select at least one explicit data scope.')
    return {'version': 1, 'scope': 'legacy-offline-inventory', 'migration_ready': False, 'scopes': scopes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', required=True)
    parser.add_argument('--confirm-offline-copy', action='store_true')
    for role in DATABASES:
        parser.add_argument('--' + role + '-root', action='append', default=[])
    arguments = parser.parse_args()
    selections = [(role, relative) for role in DATABASES for relative in getattr(arguments, role + '_root')]
    try:
        result = inventory(arguments.snapshot, selections, arguments.confirm_offline_copy)
    except InventoryError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error, ValueError):
        print('Offline inventory failed; verify the copied asset types, database integrity and access permissions.', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
