"""Maker-checker approval primitives for high-risk chama operations.

Callers must request approval before applying the underlying financial mutation, then
perform that mutation only after an APPROVED request has been independently reviewed.
"""
import json
from services import iso, now_utc

class ApprovalError(ValueError):
    pass

def request_approval(db, chama_id, request_type, entity_type, entity_id, requested_by, reason, payload=None, now=None):
    reason = (reason or '').strip()
    if not reason:
        raise ApprovalError('An explanation is required for a second-official approval.')
    rid = db.insert('maker_checker_requests', chama_id=chama_id, request_type=request_type,
        entity_type=entity_type, entity_id=str(entity_id), requested_by=requested_by,
        status='PENDING', reason=reason[:500], payload=json.dumps(payload or {}, separators=(',', ':')),
        created_at=iso(now or now_utc()))
    return rid

def decide_approval(db, request_id, chama_id, reviewer_id, approve, now=None):
    row = db.one('SELECT * FROM maker_checker_requests WHERE id=? AND chama_id=?', (request_id, chama_id))
    if not row or row['status'] != 'PENDING':
        raise ApprovalError('This approval request is missing or already decided.')
    if int(row['requested_by']) == int(reviewer_id):
        raise ApprovalError('The person who requested this action cannot approve it.')
    status = 'APPROVED' if approve else 'REJECTED'
    changed = db.execute("UPDATE maker_checker_requests SET status=?,approved_by=?,decided_at=? WHERE id=? AND status='PENDING'",
        (status, reviewer_id, iso(now or now_utc()), request_id)).rowcount
    if not changed:
        raise ApprovalError('This request was decided by another official. Refresh and try again.')
    return status
