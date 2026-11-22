from concurrent.futures import ThreadPoolExecutor

from app.bundles import sample_set
from app.dispatch.rechecks import file_supplement
from app.dispatch.signatures import sign
from app.ledger.replay import replay

LATE = 'ticket,truck,signed_kg,document\nS04,FT-18,8,R21\nT01,FT-12,80.9,R01B\nT07,FT-12,79.40,R07\nLATE9,FT-18,5,R22\n'


def signed_batch(client):
    batch = client.post('/api/shifts',json={'sample':'dirty'}).json['batch']
    client.post(f'/api/shifts/{batch}/sign')
    return batch


def file(client, batch, text=LATE):
    return client.post(f'/api/shifts/{batch}/supplements',json={'receipts':text})


def test_unsigned_shift_rejects_supplement(client):
    batch = client.post('/api/shifts',json={'sample':'dirty'}).json['batch']
    response = file(client,batch)
    assert (response.status_code,response.json['error'])==(409,'NOT_SIGNED')


def test_unknown_batch_rejects_supplement(client):
    response = client.post('/api/shifts/no-such/supplements',json={'receipts':LATE})
    assert (response.status_code,response.json['error'])==(404,'NO_BATCH')
    assert client.get('/api/shifts/no-such/supplements').json['error']=='NO_BATCH'


def test_bad_supplement_body(client):
    batch = signed_batch(client)
    response = client.post(f'/api/shifts/{batch}/supplements',json={'receipt':'x'})
    assert (response.status_code,response.json['error'])==(422,'SUPPLEMENT')


def test_late_receipt_recalculates_missing_signature(client):
    batch = signed_batch(client)
    response = file(client,batch)
    assert response.status_code==201 and response.json['reused'] is False
    conclusion, = response.json['conclusions']
    assert (conclusion['ticket'],conclusion['before'],conclusion['after'])==('S04','NO_RECEIPT','MATCH')
    assert (conclusion['signed_g'],conclusion['meter_g'],conclusion['allowance_g'],
            conclusion['variance_g'])==(8000,8000,100,0)
    assert response.json['still_missing']==[]


def test_supplement_beyond_allowance_becomes_sign_diff(client):
    batch = signed_batch(client)
    conclusion, = file(client,batch,'ticket,truck,signed_kg,document\nS04,FT-18,8.2,R21\n').json['conclusions']
    assert (conclusion['after'],conclusion['variance_g'])==('SIGN_DIFF',200)


def test_supplement_wrong_truck_becomes_sign_truck(client):
    batch = signed_batch(client)
    conclusion, = file(client,batch,'ticket,truck,signed_kg,document\nS04,FT-12,8,R21\n').json['conclusions']
    assert (conclusion['after'],conclusion['variance_g'],conclusion['receipt_truck'])==('SIGN_TRUCK',None,'FT-12')


def test_conflicting_supplement_keeps_all_originals(client):
    batch = signed_batch(client)
    result = file(client,batch).json
    disputed, = result['disputed']
    assert (disputed['ticket'],disputed['before'])==('T01','MATCH')
    kept = {(copy['origin'],copy['signed_g'],copy['document']) for copy in disputed['kept']}
    assert kept=={('archive',80400,'R01'),('supplement',80900,'R01B')}
    assert all(row['ticket']!='T01' for row in result['conclusions'])


def test_identical_supplement_is_duplicate_not_rewrite(client):
    batch = signed_batch(client)
    result = file(client,batch).json
    duplicate, = result['duplicates']
    assert (duplicate['ticket'],duplicate['origin'],duplicate['kept_line'])==('T07','archive',5)
    assert all(row['ticket']!='T07' for row in result['conclusions'])


def test_supplement_internal_conflict_stays_pending(client):
    batch = signed_batch(client)
    text = 'ticket,truck,signed_kg,document\nS04,FT-18,8,R21\nS04,FT-18,8.3,R21B\n'
    result = file(client,batch,text).json
    assert result['conclusions']==[]
    disputed, = result['disputed']
    assert disputed['ticket']=='S04' and disputed['before']=='NO_RECEIPT'
    assert {copy['signed_g'] for copy in disputed['kept']}=={8000,8300}
    assert result['still_missing']==[]


def test_unknown_ticket_supplement_unmatched(client):
    batch = signed_batch(client)
    unmatched, = file(client,batch).json['unmatched']
    assert (unmatched['ticket'],unmatched['code'])==('LATE9','NO_METER')


def test_fault_ticket_supplement_held_not_recalculated(client):
    batch = signed_batch(client)
    result = file(client,batch,'ticket,truck,signed_kg,document\nT04,FT-12,7.5,R24\n').json
    held, = result['held']
    assert (held['ticket'],held['state'],held['signed_g'])==('T04','REVERSE',7500)
    assert result['conclusions']==[]


def test_archived_conflict_supplement_stays_disputed(client):
    batch = signed_batch(client)
    result = file(client,batch,'ticket,truck,signed_kg,document\nS03,FT-18,8,R25\n').json
    disputed, = result['disputed']
    assert (disputed['ticket'],disputed['before'])==('S03','CONFLICT')
    kept = {(copy['origin'],copy['signed_g']) for copy in disputed['kept']}
    assert kept=={('archive',8000),('archive',8200),('supplement',8000)}


def test_retry_same_supplement_not_refiled(client):
    batch = signed_batch(client)
    first = file(client,batch)
    repeat = file(client,batch)
    assert (first.status_code,repeat.status_code)==(201,200)
    assert repeat.json==dict(first.json,reused=True)
    listed = client.get(f'/api/shifts/{batch}/supplements').json['supplements']
    assert len(listed)==1 and listed[0]['recalculated']==1 and listed[0]['checksum'] is None


def test_later_batch_same_copy_not_refiled(client):
    batch = signed_batch(client)
    file(client,batch,'ticket,truck,signed_kg,document\nS04,FT-18,8,R21\n')
    result = file(client,batch,'ticket,truck,signed_kg,document\nS04,FT-18,8,R21\nT04,FT-12,7.5,R24\n').json
    assert [row['ticket'] for row in result['conclusions']]==[]
    duplicate, = result['duplicates']
    assert (duplicate['ticket'],duplicate['origin'])==('S04','supplement')
    assert [row['ticket'] for row in result['held']]==['T04']


def test_later_batch_conflicting_copy_disputed(client):
    batch = signed_batch(client)
    file(client,batch,'ticket,truck,signed_kg,document\nS04,FT-18,8,R21\n')
    result = file(client,batch,'ticket,truck,signed_kg,document\nS04,FT-18,8.3,R21B\n').json
    assert result['conclusions']==[]
    disputed, = result['disputed']
    assert (disputed['ticket'],disputed['before'])==('S04','MATCH')
    assert {(copy['origin'],copy['signed_g']) for copy in disputed['kept']}=={('supplement',8000),('supplement',8300)}


def test_recheck_issue_export_and_original_still_verifiable(client):
    batch = signed_batch(client)
    supplement = file(client,batch).json['supplement']
    issued = client.post(f'/api/shifts/{batch}/supplements/{supplement}/sign').json
    original = client.get(f'/api/shifts/{batch}/export').json
    assert original['verified'] is True and issued['original']==original['checksum']
    assert (issued['recalculated'],issued['disputed'],issued['still_missing'])==(1,1,0)
    exported = client.get(f'/api/shifts/{batch}/supplements/{supplement}/export')
    assert exported.json['verified'] is True and exported.json['checksum']==issued['checksum']
    slip = exported.json['slip']
    assert slip['document']=='机坪加注补核单'
    assert slip['original']=={'document':'机坪加注证据单','batch':batch,'checksum':original['checksum']}
    assert slip['conclusions'][0]['ticket']=='S04' and slip['disputed'][0]['ticket']=='T01'
    assert exported.headers['Content-Disposition'].startswith('attachment;')


def test_repeat_recheck_sign_not_issued_twice(client):
    batch = signed_batch(client)
    supplement = file(client,batch).json['supplement']
    first = client.post(f'/api/shifts/{batch}/supplements/{supplement}/sign').json
    second = client.post(f'/api/shifts/{batch}/supplements/{supplement}/sign').json
    assert second==dict(first,reused=True)
    listed = client.get(f'/api/shifts/{batch}/supplements').json['supplements']
    assert listed[0]['checksum']==first['checksum']


def test_recheck_export_requires_sign(client):
    batch = signed_batch(client)
    supplement = file(client,batch).json['supplement']
    response = client.get(f'/api/shifts/{batch}/supplements/{supplement}/export')
    assert (response.status_code,response.json['error'])==(409,'NOT_SIGNED')


def test_unknown_supplement_404(client):
    batch = signed_batch(client)
    for path in (f'/api/shifts/{batch}/supplements/nope',
                 f'/api/shifts/{batch}/supplements/nope/sign',
                 f'/api/shifts/{batch}/supplements/nope/export'):
        response = client.get(path) if path.endswith(('nope','export')) else client.post(path)
        assert (response.status_code,response.json['error'])==(404,'NO_SUPPLEMENT')


def test_tampered_original_blocks_recheck(client,ramp):
    batch = signed_batch(client)
    supplement = file(client,batch).json['supplement']
    with ramp.book.connection() as db:
        db.execute("UPDATE stage_rows SET body='{}' WHERE phase='balance'")
    response = client.post(f'/api/shifts/{batch}/supplements/{supplement}/sign')
    assert (response.status_code,response.json['error'])==(409,'ORIGINAL_UNVERIFIED')
    assert client.get(f'/api/shifts/{batch}/export').json['verified'] is False


def test_late_sample_files_against_signed_batch(client):
    batch = signed_batch(client)
    response = client.post(f'/api/shifts/{batch}/supplements',json={'sample':'late'})
    assert response.status_code==201
    result = response.json
    counts = {key:len(result[key]) for key in ('conclusions','disputed','duplicates','unmatched')}
    assert counts=={'conclusions':1,'disputed':1,'duplicates':1,'unmatched':1}


def test_concurrent_identical_filings_store_once(ramp):
    batch = replay(ramp.book,sample_set('dirty'))['batch']
    sign(ramp.book,batch)
    with ThreadPoolExecutor(5) as pool:
        results = list(pool.map(lambda _:file_supplement(ramp.book,batch,LATE.encode()),range(7)))
    assert sum(not row['reused'] for row in results)==1
    assert len({row['supplement'] for row in results})==1


def test_sign_page_offers_recheck_panel(client):
    client.post('/api/shifts',json={'sample':'dirty'})
    shown = client.get('/ops/sign').get_data(as_text=True)
    assert '晚到签收联补核' in shown and '签发补核结果' in shown
