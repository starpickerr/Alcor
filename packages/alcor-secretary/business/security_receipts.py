"""Small owner-local receipt store for approved destructive operations."""
import hashlib
import json
import re


def operation_digest(method, arguments):
    return hashlib.sha256(json.dumps([method, arguments], sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def validate_operation_id(operation_id):
    if not isinstance(operation_id, str) or not re.fullmatch(r'[0-9a-f]{64}', operation_id):
        raise ValueError('invalid operation identity')


def read_receipt(db, operation_id, method, arguments):
    validate_operation_id(operation_id)
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='alcor_security_receipts'").fetchone():
        return None
    row = db.execute('SELECT * FROM alcor_security_receipts WHERE operation_id=?', (operation_id,)).fetchone()
    if not row:
        return None
    if row['method'] != method or row['digest'] != operation_digest(method, arguments):
        raise ValueError('idempotency conflict: different approved operation')
    return {'operationId': operation_id, 'method': method, 'targetId': row['target_id']}


def save_receipt(db, operation_id, method, arguments, target_id):
    db.execute('CREATE TABLE IF NOT EXISTS alcor_security_receipts (operation_id TEXT PRIMARY KEY, method TEXT NOT NULL, digest TEXT NOT NULL, target_id TEXT NOT NULL)')
    db.execute('INSERT INTO alcor_security_receipts VALUES (?,?,?,?)', (operation_id, method, operation_digest(method, arguments), target_id))
