"""Exercise auto-sync stop/disable gate using isolated state, no Apple account."""
import runpy
import sys
import threading

sys.path[:0]=['packages/alcor-secretary','packages/alcor-apple-sync/python']
modules=runpy.run_path('product/tests/apple-contracts.py')
for kind in ('calendar','reminders'):
    case=modules['Contracts']('test_unconfirmed_occurrence_delete_preserves_instances_after_restart');case.setUp()
    try:
        manager=case.calendar(modules['NativeBridge'](kind)) if kind=='calendar' else case.reminder(modules['NativeBridge'](kind))
        manager._set('connected',True);manager._set('auto_enabled',True)
        calls=[];manager.sync=lambda:calls.append('sync')
        stop=threading.Event();manager._run_auto_sync(stop);assert calls==['sync']
        # An auto callback already waiting for the manager lock must recheck after disable.
        with manager._lock:
            thread=threading.Thread(target=manager._run_auto_sync,args=(stop,));thread.start()
            manager._set('auto_enabled',False);stop.set()
        thread.join(2);assert not thread.is_alive() and calls==['sync']
    finally:case.tearDown()
