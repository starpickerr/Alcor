"""Synthetic watcher restart and missed-notification reconciliation."""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'packages/alcor-apple-sync/python'))
from apple_sync.runtime import EventKitAutoRunner

class Bridge:
    def __init__(self): self.calls=0; self.restarted=threading.Event()
    def watch(self, on_change, stop_event, start_lock=None):
        self.calls += 1
        if self.calls == 1: return  # Simulate unexpected watcher exit, e.g. sleep or crash.
        self.restarted.set();stop_event.wait(2)

bridge=Bridge();sync_count=[]
runner=EventKitAutoRunner(bridge,lambda:True,lambda:sync_count.append('sync'),interval=300,watch_retry_interval=0.02)
runner.start()
try:
    assert bridge.restarted.wait(2), 'watcher did not reconnect promptly'
    deadline=time.monotonic()+2
    while len(sync_count)<2 and time.monotonic()<deadline:time.sleep(0.01)
    assert len(sync_count)>=2, 'watch outage was not followed by reconciliation'
finally:
    runner.stop()

# A deliberate stop must not be interpreted as a crash or schedule a recovery sync.
class StoppingBridge:
    def __init__(self): self.started=threading.Event();self.calls=0
    def watch(self, on_change, stop_event, start_lock=None):
        self.calls+=1;self.started.set();stop_event.wait(2)

bridge=StoppingBridge();sync_count=[]
runner=EventKitAutoRunner(bridge,lambda:True,lambda:sync_count.append('sync'),interval=300,watch_retry_interval=0.02)
runner.start()
assert bridge.started.wait(2)
deadline=time.monotonic()+2
while not sync_count and time.monotonic()<deadline:time.sleep(0.01)
runner.stop();before=len(sync_count);time.sleep(0.1)
assert bridge.calls==1 and len(sync_count)==before, 'explicit stop restarted or reconciled'

# If a watcher returns after its connection is disabled, neither recovery nor reconnect occurs.
class DisabledBridge:
    def __init__(self):self.started=threading.Event();self.release=threading.Event();self.calls=0
    def watch(self,on_change,stop_event,start_lock=None):self.calls+=1;self.started.set();self.release.wait(2)

connected=[True];bridge=DisabledBridge();sync_count=[]
runner=EventKitAutoRunner(bridge,lambda:connected[0],lambda:sync_count.append('sync'),interval=300,watch_retry_interval=0.02)
runner.start();assert bridge.started.wait(2)
deadline=time.monotonic()+2
while not sync_count and time.monotonic()<deadline:time.sleep(0.01)
connected[0]=False;before=len(sync_count);bridge.release.set();time.sleep(0.1)
assert bridge.calls==1 and len(sync_count)==before, 'disabled connection restarted or reconciled'
runner.stop()

# An old helper without a controlled watch uses the ordinary polling cadence, not a retry loop.
class OldBridge:
    def __init__(self):self.calls=0;self.called=threading.Event()
    def watch(self,on_change,stop_event,start_lock=None):self.calls+=1;self.called.set();return 'unsupported'

bridge=OldBridge();sync_count=[]
runner=EventKitAutoRunner(bridge,lambda:True,lambda:sync_count.append('sync'),interval=300,watch_retry_interval=0.02)
runner.start();assert bridge.called.wait(2);time.sleep(0.1)
assert bridge.calls==1 and len(sync_count)==1, 'old helper caused a fast reconnect or extra sync'
runner.stop()
