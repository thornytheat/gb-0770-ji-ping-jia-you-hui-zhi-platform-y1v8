import json
from datetime import datetime, timezone

from app.diagnostics import RampError
from app.ledger.manifest import digest, lock, serialize
from app.ledger.replay import shift
from app.settlement.recheck import supplement_reconciliation
from .document import compile_slip


def _signed_archive(book, batch):
    archive = shift(book, batch)
    with book.connection() as db:
        row = db.execute('SELECT checksum FROM signatures WHERE digest=?', (batch,)).fetchone()
    if row is None:
        raise RampError('NOT_SIGNED', 409)
    return archive, row[0]


def _load(book, batch, supplement):
    with book.connection() as db:
        row = db.execute('SELECT body FROM supplements WHERE digest=? AND batch=?', (supplement, batch)).fetchone()
    if row is None:
        raise RampError('NO_SUPPLEMENT', 404)
    return json.loads(row[0])


def _prior_copies(db, batch):
    prior = {}
    for supplement, body in db.execute('SELECT digest, body FROM supplements WHERE batch=? ORDER BY rowid', (batch,)):
        result = json.loads(body)
        for item in result['conclusions'] + result['held']:
            prior[item['ticket']] = {'state': item.get('after') or item['state'], 'disputed': False,
                                     'copies': [{'origin': 'supplement', 'line': item['receipt_line'],
                                                 'truck': item['receipt_truck'], 'signed_g': item['signed_g'],
                                                 'document': item['document'], 'supplement': supplement}]}
        for item in result['disputed']:
            copies = [dict(copy, supplement=supplement) for copy in item['kept'] if copy['origin'] == 'supplement']
            prior[item['ticket']] = {'state': 'DISPUTED', 'disputed': True, 'copies': copies}
    return prior


def file_supplement(book, batch, raw):
    archive, _ = _signed_archive(book, batch)
    supplement = lock({'batch': batch, 'receipts_sha256': digest(raw)})
    with book.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        saved = db.execute('SELECT body FROM supplements WHERE digest=?', (supplement,)).fetchone()
        reused = saved is not None
        if reused:
            body = json.loads(saved[0])
        else:
            body = dict(supplement_reconciliation(archive, raw, _prior_copies(db, batch)),
                        batch=batch, supplement=supplement,
                        filed_at=datetime.now(timezone.utc).isoformat())
            db.execute('INSERT INTO supplements VALUES (?,?,?)', (supplement, batch, serialize(body)))
    return dict(body, reused=reused)


def supplement_result(book, batch, supplement):
    shift(book, batch)
    return _load(book, batch, supplement)


def list_supplements(book, batch):
    shift(book, batch)
    with book.connection() as db:
        rows = db.execute('SELECT s.body, r.checksum FROM supplements s LEFT JOIN rechecks r ON r.digest=s.digest '
                          'WHERE s.batch=? ORDER BY s.rowid DESC', (batch,)).fetchall()
    supplements = []
    for body, checksum in rows:
        result = json.loads(body)
        supplements.append({'supplement': result['supplement'], 'filed_at': result['filed_at'],
                            'input': result['input'], 'accepted': result['accepted'],
                            'recalculated': len(result['conclusions']), 'disputed': len(result['disputed']),
                            'still_missing': len(result['still_missing']), 'checksum': checksum})
    return supplements


def compile_recheck_slip(body, checksum):
    return {'document': '机坪加注补核单',
            'original': {'document': '机坪加注证据单', 'batch': body['batch'], 'checksum': checksum},
            'supplement': {'digest': body['supplement'], 'filed_at': body['filed_at'], 'input': body['input'],
                           'accepted': body['accepted'], 'isolated': body['isolated'], 'duplicate': body['duplicate']},
            'conclusions': body['conclusions'], 'disputed': body['disputed'], 'held': body['held'],
            'duplicates': body['duplicates'], 'unmatched': body['unmatched'],
            'still_missing': body['still_missing'], 'retained': body['quarantine']}


def issue_recheck(book, batch, supplement):
    archive, checksum = _signed_archive(book, batch)
    body = _load(book, batch, supplement)
    if lock(compile_slip(archive)) != checksum:
        raise RampError('ORIGINAL_UNVERIFIED', 409)
    document = compile_recheck_slip(body, checksum)
    with book.connection() as db:
        inserted = db.execute('INSERT INTO rechecks VALUES (?,?) ON CONFLICT(digest) DO NOTHING',
                              (supplement, lock(document))).rowcount
        saved = db.execute('SELECT checksum FROM rechecks WHERE digest=?', (supplement,)).fetchone()[0]
    return {'batch': batch, 'supplement': supplement, 'checksum': saved, 'original': checksum,
            'reused': not bool(inserted), 'recalculated': len(body['conclusions']),
            'disputed': len(body['disputed']), 'still_missing': len(body['still_missing'])}


def recheck_file(book, batch, supplement):
    _, checksum = _signed_archive(book, batch)
    body = _load(book, batch, supplement)
    with book.connection() as db:
        row = db.execute('SELECT checksum FROM rechecks WHERE digest=?', (supplement,)).fetchone()
    if row is None:
        raise RampError('NOT_SIGNED', 409)
    document = compile_recheck_slip(body, checksum)
    return {'checksum': row[0], 'verified': row[0] == lock(document), 'slip': document}
