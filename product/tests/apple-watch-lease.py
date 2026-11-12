"""Pure synthetic watcher lease; no EventKit access or real helper launch."""
import subprocess
import shutil
import tempfile
from pathlib import Path

source = Path(__file__).resolve().parents[2] / 'packages/alcor-apple-sync/python/apple_sync/native/OccurrenceIdentity.swift'
with tempfile.TemporaryDirectory(prefix='alcor-watch-lease-') as directory:
    main = Path(directory) / 'main.swift'
    main.write_text('''import Foundation
var lease = WatchLease(now: 0, timeout: 10)
assert(!lease.shouldStop("0", now: 0))
assert(!lease.shouldStop("0", now: 9))
assert(lease.shouldStop("0", now: 10))
var active = WatchLease(now: 0, timeout: 10)
assert(!active.shouldStop("one", now: 0))
assert(!active.shouldStop("two", now: 9))
assert(!active.shouldStop("two", now: 18))
assert(active.shouldStop("two", now: 19))
var stopped = WatchLease(now: 0, timeout: 10)
assert(stopped.shouldStop("stop", now: 0))
var missing = WatchLease(now: 0, timeout: 10)
assert(missing.shouldStop(nil, now: 0))
''')
    executable = Path(directory) / 'watch-lease'
    staged = Path(directory) / source.name
    shutil.copyfile(source, staged)
    subprocess.run(['/usr/bin/swiftc', str(staged), str(main), '-o', str(executable)], check=True)
    subprocess.run([str(executable)], check=True)
