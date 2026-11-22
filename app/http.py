from flask import Blueprint, current_app, jsonify, request
from app.bundles import late_sample, submitted
from app.diagnostics import RampError
from app.dispatch.rechecks import (file_supplement, issue_recheck, list_supplements,
                                   recheck_file, supplement_result)
from app.dispatch.signatures import evidence_file, sign
from app.ledger.replay import list_shifts,replay,shift
from app.shift import compute

routes = Blueprint('ramp_api',__name__)


def evaluate(stage):
    return compute(submitted(),stage)[stage]


@routes.route('/api/intake',methods=['GET','POST'])
def intake():
    return evaluate('intake')


@routes.route('/api/versions',methods=['GET','POST'])
def versions():
    return evaluate('versions')


@routes.route('/api/readings',methods=['GET','POST'])
def readings():
    return evaluate('readings')


@routes.route('/api/mass',methods=['GET','POST'])
def mass():
    return evaluate('mass')


@routes.route('/api/balance',methods=['GET','POST'])
def balance():
    return evaluate('balance')


@routes.get('/api/shifts')
def shifts():
    return {'shifts':list_shifts(current_app.book)}


@routes.post('/api/shifts')
def archive():
    result = replay(current_app.book,submitted())
    return result,200 if result['reused'] else 201


@routes.get('/api/shifts/<batch>')
def load(batch):
    return shift(current_app.book,batch)


@routes.post('/api/shifts/<batch>/sign')
def issue(batch):
    return sign(current_app.book,batch)


@routes.get('/api/shifts/<batch>/export')
def export(batch):
    result = jsonify(evidence_file(current_app.book,batch))
    result.headers['Content-Disposition'] = f'attachment; filename="fuel-{batch}.json"'
    return result


@routes.get('/api/shifts/<batch>/supplements')
def supplements(batch):
    return {'supplements':list_supplements(current_app.book,batch)}


@routes.post('/api/shifts/<batch>/supplements')
def file_late(batch):
    body = request.get_json(silent=True)
    if isinstance(body,dict) and set(body)=={'sample'} and body['sample']=='late':
        raw = late_sample()
    elif isinstance(body,dict) and set(body)=={'receipts'} and isinstance(body['receipts'],str):
        try:
            raw = body['receipts'].encode()
        except UnicodeEncodeError as exc:
            raise RampError('FILE') from exc
    else:
        raise RampError('SUPPLEMENT')
    result = file_supplement(current_app.book,batch,raw)
    return result,200 if result['reused'] else 201


@routes.get('/api/shifts/<batch>/supplements/<supplement>')
def load_late(batch, supplement):
    return supplement_result(current_app.book,batch,supplement)


@routes.post('/api/shifts/<batch>/supplements/<supplement>/sign')
def issue_late(batch, supplement):
    return issue_recheck(current_app.book,batch,supplement)


@routes.get('/api/shifts/<batch>/supplements/<supplement>/export')
def export_late(batch, supplement):
    result = jsonify(recheck_file(current_app.book,batch,supplement))
    result.headers['Content-Disposition'] = f'attachment; filename="fuel-{batch}-recheck-{supplement[:12]}.json"'
    return result
