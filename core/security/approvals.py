"""Core-only approval receipts scoped to one validated tool invocation."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import time

_pending = ContextVar('stella_pending_operation_receipt', default=None)
_active = ContextVar('stella_active_operation_receipt', default=None)
_hub_operation = ContextVar('stella_approved_hub_operation', default=None)


def clear_pending():
    _pending.set(None)


def remember(descriptor, binding, arguments):
    _pending.set((descriptor, binding, deepcopy(arguments), time.monotonic() + 300))


@contextmanager
def approved_invocation():
    receipt = _pending.get()
    _pending.set(None)
    token = _active.set(receipt)
    try:
        yield
    finally:
        _active.reset(token)


@contextmanager
def agent_hub_operation_scope(public_arguments, method, payload):
    """Only the Core adapter derives a start from its exact approved request.

    The generated execution ID/workspace are not model-selected approval aliases.
    Bind the complete derived payload, including provider, goal and nested budget.
    """
    if method not in {'select_provider', 'provider_start'}:
        raise ValueError('Unsupported Agent Hub approval translation')
    receipt = _active.get()
    if receipt and receipt[0].capability_id == 'plugin:stella.agent-hub' and receipt[0].operation == 'system_execute':
        if receipt[2] != public_arguments:
            raise PermissionError('Agent Hub request changed after approval')
        token = _hub_operation.set((receipt, deepcopy({'method': method, 'input': payload})))
        try:
            yield
        finally:
            _hub_operation.reset(token)
    else:
        yield


def _targets(arguments):
    values = arguments.get('input') if isinstance(arguments.get('input'), dict) else arguments
    targets = set()
    primary = {'id', 'todo_id', 'event_id', 'ledger_id', 'task_id', 'provider_config_id', 'execution_id'}
    resources = {'account_id', 'source_id', 'parent_id', 'prerequisite_id', 'receiver', 'path'}
    for name, value in values.items():
        if isinstance(value, str) and value and name in primary | resources:
            targets.add(('target' if name in primary else name, value))
    return targets


def inherited(descriptor, binding, arguments):
    receipt = _active.get()
    if receipt is None:
        return False
    approved, expected, original, expiry = receipt
    if (time.monotonic() > expiry or expected != binding or descriptor.capability_id != approved.capability_id
            or descriptor.operation != approved.operation or descriptor.source != approved.source):
        return False
    if descriptor.source == 'mcp':
        return original == arguments
    if descriptor.source != 'plugin':
        return False
    if descriptor.capability_id == 'plugin:stella.agent-hub' and descriptor.operation == 'system_execute':
        derived = _hub_operation.get()
        return original == arguments or bool(derived and derived[0] is receipt and derived[1] == arguments)
    # Only trusted Core adapters translate tool actions into service methods.
    # A service can retain the approved target; another object needs approval.
    return _targets(arguments) <= _targets(original)
