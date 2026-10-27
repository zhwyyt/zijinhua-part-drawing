"""Independent native provenance and feature proof obligations for Tekla 2017."""

from .native_facts import build_native_facts
from .nc_witness import _time


def native_sources(before, after=None, capture=None):
    """NC files, numbering, export settings and API success are not native provenance."""
    first = build_native_facts(before)
    last = build_native_facts(after) if after is not None else None
    common = []
    if first['readStatus'] != 'COMPLETE' or (last and last['readStatus'] != 'COMPLETE'):
        common.append({'code': 'RAW_READ_INCOMPLETE', 'message': 'Native evidence is incomplete'})
    try:
        times = [before['startedAtUtc'], before['completedAtUtc']]
        if after is not None:
            times += [after['startedAtUtc'], after['completedAtUtc']]
        stamps = [_time(t) for t in times]
        if stamps != sorted(stamps):
            raise ValueError('Native read order invalid')
    except (KeyError, ValueError, TypeError):
        common.append({'code': 'NATIVE_TIMELINE_INVALID', 'message': 'Native read window not established'})
    if after is not None and before.get('model') != after.get('model'):
        common.append({'code': 'MODEL_CHANGED', 'message': 'Model identity changed between native reads'})
    if capture is not None:
        ids = capture.get('requestedIds')
        if (not isinstance(ids, list) or not ids or any(type(i) is not int for i in ids)
                or len(ids) != len(set(ids))):
            raise ValueError('Unique numeric requestedIds required')
        if any(sorted(p['id'] for p in raw['parts']) != sorted(ids) for raw in (before, after)):
            common.append({'code': 'CAPTURE_PART_SET_MISMATCH', 'message': 'Native reads differ from requested IDs'})
        if capture.get('beforeExitCode') != 0 or capture.get('afterExitCode') != 0:
            common.append({'code': 'NATIVE_READ_FAILED', 'message': 'Native reader reported failure'})
        if (capture.get('restoreError') or capture.get('selectionRestored') is not True
                or capture.get('selectionBefore') is None or capture.get('selectionAfter') is None
                or sorted(capture['selectionBefore']) != sorted(capture['selectionAfter'])):
            common.append({'code': 'SELECTION_NOT_RESTORED', 'message': 'Capture environment restoration unconfirmed'})
    after_by_id = {p['id']: p for p in after['parts']} if after is not None else {}
    result = []
    for index, part in enumerate(first['parts']):
        issues = list(common)
        if part['sourceIdentity']['status'] != 'READ_CONSISTENT':
            issues.append({'code': 'INSTANCE_IDENTITY_UNCONFIRMED', 'message': 'Target identity/version unavailable'})
        if after is not None and before['parts'][index] != after_by_id.get(part['id']):
            issues.append({'code': 'MODEL_PART_CHANGED', 'message': 'Native part changed between reads'})
        if any(i['code'] == 'PART_POSITION_UNRESOLVED' and i['sourceRef'].startswith(f'#/parts/{index}/')
               for i in first['issues']):
            issues.append({'code': 'PART_POSITION_UNRESOLVED', 'message': 'Exact target part position unavailable'})
        result.append({'id': part['id'], 'partPosition': part['partPosition']['value'],
                       'status': 'BLOCKED' if issues else 'READ_CONSISTENT', 'issues': issues,
                       'identity': part['sourceIdentity'], 'sourceRef': f'#/parts/{index}',
                       'comparison': 'BEFORE_AFTER' if after is not None else 'WITHIN_READ'})
    return result


def nc_source(witness=None, error=None):
    if error is not None:
        return {'status': 'UNAVAILABLE', 'issues': [{'code': 'NC_EVIDENCE_UNREADABLE', 'message': str(error)}]}
    if witness is None:
        return {'status': 'NOT_REQUESTED', 'issues': []}
    status = 'CANDIDATE' if witness['status'] == 'CANDIDATE' else 'UNAVAILABLE'
    return {'status': status, 'issues': witness['issues'], 'modelGuid': witness['modelGuid'],
            'associationBasis': witness['associationBasis'], 'instanceRelation': witness['instanceRelation'],
            'geometryRepresentation': witness['geometryRepresentation'],
            'exportLogEntries': witness['exportLogEntries'],
            'fileSha256': witness['ncFile']['sha256'] if witness['ncFile'] else None,
            'settingsBinding': 'UNCONFIRMED',
            'remainingProofs': ['EFFECTIVE_NC_SETTINGS_BINDING'] if status == 'CANDIDATE' else []}


def feature_evidence(outline, shell, thickness, groups, topology, operation, source, nc, path, final_cut=None, material=None):
    """List obligations independently of NC usability; NC never proves final surfaces/holes."""
    features = []
    matched_nc = nc.get('status') == 'MATCHED'
    cut_matched = final_cut is not None and final_cut['status'] == 'MATCHED'
    material_complete = material is not None and material['status'] == 'MATCHED'
    cut_complete = material_complete or (cut_matched and not groups and not final_cut.get('remainingProofs'))

    def add(identity, kind, refs, matched, missing, nc_rows=(), supplement=False):
        gaps = list(missing)
        if source['status'] != 'READ_CONSISTENT':
            gaps.append('NATIVE_SOURCE_UNCONFIRMED')
        conflict = nc.get('status') == 'CONFLICT' and nc.get('featureScope') == kind
        if conflict:
            gaps.append('NC_GEOMETRY_CONFLICT')
        features.append({'id': identity, 'kind': kind, 'sourceRefs': list(refs),
                         'evidenceStatus': 'MATCHED' if matched else 'UNRESOLVED',
                         'proofStatus': 'CONFLICT' if conflict else ('INSUFFICIENT' if gaps else 'SUFFICIENT'),
                         'missingProofs': gaps, 'ncEvidence': list(nc_rows),
                         'ncRole': 'SUPPLEMENTARY_MATCH' if nc_rows else
                                   ('CAN_SUPPLEMENT' if supplement else 'NOT_NEEDED')})

    segments = outline.get('segments', [])
    for index, segment in enumerate(segments):
        rows = [m for m in nc.get('outlineMapping', []) if m['segmentIndex'] == index] if matched_nc else []
        missing = [] if cut_matched or material_complete else ['EXACT_SURFACE_SEMANTICS']
        if shell['status'] != 'MATCHED':
            missing.append('FINAL_BOUNDARY_COVERAGE')
        add(f'outline:{index}', segment['kind'], segment['sourceRefs'] + [path+'/finalSolid'],
            shell['status'] == 'MATCHED', missing, rows, segment['kind'] == 'ARC')
    if not segments:
        add('outline', 'OUTLINE', [path+'/modelingContour', path+'/finalSolid'], False,
            ['ANALYTIC_OUTLINE_UNRESOLVED', 'FINAL_BOUNDARY_COVERAGE'])
    add('thickness', 'THICKNESS', thickness.get('sourceRefs', [path+'/finalSolid', path+'/profile']),
        thickness['status'] == 'MATCHED', [] if thickness['status'] == 'MATCHED' else ['THICKNESS_UNRESOLVED'])
    for group in groups:
        candidates = group['candidates']
        if not isinstance(candidates, list):
            if group['classification'] == 'ATTACHMENT_STUD':
                add(f"group:{group['id']}", 'ATTACHMENT_STUD', [group['sourceRef']], True, [])
                features[-1]['rule'] = 'STUD-NO-HOLE-01'
                features[-1]['meaning'] = 'Surface-welded attachment; not a plate hole or assembly welding verification'
                continue
            add(f"group:{group['id']}", group['classification'], [group['sourceRef']], False,
                ['HOLE_PARAMETERS_UNRESOLVED'])
            continue
        effect_matched = group['solidCheck']['status'] == 'MATCHED'
        composite = group.get('effectEvidence', {})
        confirmed = composite.get('status') == 'COMPOSITE_CONFIRMED' or material_complete
        for hole in candidates:
            rows = [m for m in nc.get('holeMapping', []) if m['holeId'] == hole['id']] if matched_nc else []
            missing = [] if confirmed else ['HOLE_EFFECT_UNPROVEN'] + composite.get('missingProofs', [])
            add('hole:'+hole['id'], hole['kind'], hole['sourceRefs'] + [group['sourceRef']+'/solidWithHighAccuracy'],
                confirmed or effect_matched, missing, rows, not confirmed)
            features[-1]['effectEvidence'] = composite
            if material_complete:
                features[-1]['finalStateEvidence'] = group['finalStateEvidence']
            if composite.get('status') == 'CONFLICT':
                features[-1]['proofStatus'] = 'CONFLICT'
    if material_complete:
        for index, hole in enumerate(material['holes']):
            rows = [m for m in nc.get('holeMapping', []) if m['holeId'] == 'material-result:'+str(index)] if matched_nc else []
            add('materialHole:'+hole['id'], hole['kind'], hole['sourceRefs']+[path+'/finalSolid'], True, [], rows)
    elif cut_matched:
        for index, hole in enumerate(final_cut['holes']):
            rows = [m for m in nc.get('holeMapping', []) if m['holeId'] == 'cut-result:'+str(index)] if matched_nc else []
            add('cutHole:'+hole['id'], hole['kind'], hole['sourceRefs']+[path+'/finalSolid'], True, [], rows)
    no_holes = not groups and shell['status'] == 'MATCHED' and operation['status'] == 'EMPTY_SET'
    add('holeInventory', 'HOLE_INVENTORY', [path+'/boltGroups', path+'/finalSolid', path+'/operations'],
        no_holes or cut_complete, [] if no_holes or cut_complete else ['FINISHED_HOLE_INVENTORY_UNPROVEN'], supplement=bool(groups))
    add('operations', 'OPERATIONS', operation['sourceRefs'], operation['status'] in ('EMPTY_SET', 'MATCHED'),
        operation.get('remainingProofs', []) if operation['status'] in ('EMPTY_SET', 'MATCHED') else ['OPERATION_EFFECT_UNPROVEN'])
    add('topology', 'TOPOLOGY', [path+'/finalSolid', path+'/modelingContour', path+'/boltGroups'],
        topology['status'] == 'MATCHED', [] if cut_complete and topology['status'] == 'MATCHED' else ['FINAL_TOPOLOGY_UNPROVEN'])
    if cut_matched or material_complete:
        for feature in features:
            feature['verificationBasis'] = 'MODEL_TOLERANCE_NOT_EXACT_BREP'
            if material_complete:
                feature['materialBasis'] = material['basis']
    # A conflict can refer to a whole inventory or outline, including an extra NC hole.
    if nc.get('status') == 'CONFLICT':
        scope = nc['featureScope']
        affected = ('outline:',) if scope == 'OUTLINE' else ('hole:', 'cutHole:', 'materialHole:')
        for feature in features:
            if (feature['id'].startswith(affected) if scope in ('OUTLINE', 'HOLES') else False):
                feature['proofStatus'] = 'CONFLICT'
                feature['missingProofs'].append('NC_GEOMETRY_CONFLICT')
        add('ncConflict', scope, [path], False, ['NC_GEOMETRY_CONFLICT'])
    return features


def evidence_sufficiency(features):
    if any(f['proofStatus'] == 'CONFLICT' for f in features):
        return 'CONFLICT'
    return 'SUFFICIENT' if features and all(f['proofStatus'] == 'SUFFICIENT' for f in features) else 'INSUFFICIENT'
