#!/usr/bin/python3
"""Count synthetic EventKit invocations before delegating to the repository fake."""
import os
from pathlib import Path
import stat
import sys

home = Path(os.environ['DSH_HOME'])
info = home.lstat()
if (not home.is_absolute() or home.resolve() != home or not str(home).startswith('/private/tmp/')
        or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077):
    raise RuntimeError('Expected a private synthetic DSH_HOME under /private/tmp')
fd = os.open(home / 'fake-eventkit-calls', os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
try:
    counter = os.fstat(fd)
    if not stat.S_ISREG(counter.st_mode) or counter.st_nlink != 1 or counter.st_uid != os.getuid() or counter.st_mode & 0o077:
        raise RuntimeError('Unsafe synthetic call counter')
    os.write(fd, b'1\n')
finally:
    os.close(fd)
state = home / 'fake-eventkit-state.json'
for path in (state, Path(str(state) + '.lock')):
    try:
        existing = path.lstat()
    except FileNotFoundError:
        continue
    if not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1 or existing.st_uid != os.getuid():
        raise RuntimeError('Unsafe synthetic EventKit state')
os.environ['ALCOR_EVENTKIT_STATE'] = os.environ['STELLA_EVENTKIT_STATE'] = str(state)
fake = Path(__file__).resolve().with_name('fake-eventkit.py')
os.execv(sys.executable, [sys.executable, str(fake)])
