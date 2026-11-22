from app.diagnostics import detail
from app.intake.evidence import channel_report


def _copy(origin, line, data):
    return {'origin': origin, 'line': line, 'truck': data['truck'],
            'signed_g': data['signed_g'], 'document': data['document']}


def _same(kept, incoming):
    return all(kept[key] == incoming[key] for key in ('truck', 'signed_g', 'document'))


def supplement_reconciliation(archive, raw, prior=None):
    """晚到签收联进入原班次对账。

    有效补件重算缺签票的核销结论；与留存原件冲突的票全部原件留存、仍待核；
    与留存件完全一致的补件记重复，不再核销。prior 为先前补件批次的留存件。
    """
    prior = prior or {}
    sheet = channel_report('receipts', raw)
    stages = archive['stages']
    findings = {row['ticket']: row for row in stages['balance']['receipts']}
    admitted = {row['line']: row['data'] for row in stages['intake']['sheets']['receipts']['rows']}
    quarantined = {}
    for row in stages['intake']['sheets']['receipts']['quarantine']:
        if row['code'] == 'CONFLICT':
            quarantined.setdefault(row['data']['ticket'], []).append(row)

    def archive_copies(ticket):
        finding = findings.get(ticket)
        copies = []
        if finding and finding['document'] is not None:
            line = finding['receipt_lines'][0]
            copies.append(_copy('archive', line, admitted[line]))
        copies.extend(_copy('archive', row['line'], row['data']) for row in quarantined.get(ticket, []))
        return copies

    result = {'conclusions': [], 'disputed': [], 'held': [], 'duplicates': [], 'unmatched': []}

    def dispute(ticket, truck, before, kept):
        result['disputed'].append({'ticket': ticket, 'truck': truck, 'before': before,
                                   'kept': kept, 'reason': detail('DISPUTED')['note']})

    for row in sheet['rows']:
        data, line, ticket = row['data'], row['line'], row['data']['ticket']
        incoming = _copy('supplement', line, data)
        finding = findings.get(ticket)
        if ticket in prior:
            filed = prior[ticket]
            if not filed['disputed'] and _same(filed['copies'][0], incoming):
                result['duplicates'].append({'ticket': ticket, 'line': line,
                                             'kept_line': filed['copies'][0]['line'], 'origin': 'supplement'})
            else:
                truck = finding['truck'] if finding else filed['copies'][0]['truck']
                dispute(ticket, truck, filed['state'],
                        archive_copies(ticket) + filed['copies'] + [incoming])
            continue
        if finding is None:
            result['unmatched'].append(dict(detail('NO_METER'), ticket=ticket, truck=data['truck'],
                                            line=line, signed_g=data['signed_g'], document=data['document']))
            continue
        if finding['document'] is not None:
            original = archive_copies(ticket)[0]
            if _same(original, incoming):
                result['duplicates'].append({'ticket': ticket, 'line': line,
                                             'kept_line': original['line'], 'origin': 'archive'})
            else:
                dispute(ticket, finding['truck'], finding['state'], [original, incoming])
            continue
        if ticket in quarantined:
            dispute(ticket, finding['truck'], finding['state'], archive_copies(ticket) + [incoming])
            continue
        if finding['state'] == 'NO_RECEIPT':
            variance, after = None, 'SIGN_TRUCK'
            if data['truck'] == finding['truck']:
                variance = data['signed_g'] - finding['meter_g']
                after = 'SIGN_DIFF' if abs(variance) > finding['allowance_g'] else 'MATCH'
            result['conclusions'].append({'ticket': ticket, 'truck': finding['truck'], 'before': 'NO_RECEIPT',
                                          'after': after, 'signed_g': data['signed_g'],
                                          'meter_g': finding['meter_g'], 'allowance_g': finding['allowance_g'],
                                          'variance_g': variance, 'document': data['document'],
                                          'receipt_line': line, 'receipt_truck': data['truck'],
                                          'reason': detail(after)['note'] if after != 'MATCH' else '误差带内'})
            continue
        result['held'].append({'ticket': ticket, 'truck': finding['truck'], 'state': finding['state'],
                               'signed_g': data['signed_g'], 'document': data['document'],
                               'receipt_line': line, 'receipt_truck': data['truck'],
                               'reason': detail(finding['state'])['note']})

    conflicts = {}
    for row in sheet['quarantine']:
        if row['code'] == 'CONFLICT':
            conflicts.setdefault(row['data']['ticket'], []).append(row)
    for ticket in sorted(conflicts):
        incoming = [_copy('supplement', row['line'], row['data']) for row in conflicts[ticket]]
        finding = findings.get(ticket)
        if ticket in prior:
            filed = prior[ticket]
            truck = finding['truck'] if finding else filed['copies'][0]['truck']
            dispute(ticket, truck, filed['state'], archive_copies(ticket) + filed['copies'] + incoming)
        elif finding is None:
            for row in conflicts[ticket]:
                result['unmatched'].append(dict(detail('NO_METER'), ticket=ticket, truck=row['data']['truck'],
                                                line=row['line'], signed_g=row['data']['signed_g'],
                                                document=row['data']['document']))
        else:
            dispute(ticket, finding['truck'], finding['state'], archive_copies(ticket) + incoming)

    covered = set(prior) | {item['ticket'] for key in ('conclusions', 'disputed', 'held') for item in result[key]}
    result['still_missing'] = [row['ticket'] for row in stages['balance']['receipts']
                               if row['state'] == 'NO_RECEIPT' and row['ticket'] not in covered]
    return {'input': sheet['input'], 'accepted': sheet['accepted'], 'isolated': sheet['isolated'],
            'duplicate': sheet['duplicate'], 'segments': sheet['segments'], **result,
            'quarantine': sheet['quarantine'], 'repeat_sources': sheet['repeat_sources']}
