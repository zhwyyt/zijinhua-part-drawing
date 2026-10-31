"""Reaudit native evidence and freeze source inputs without invoking any layout."""

from ..native_expressions import build_native_expression_requirements
from .contracts import canonical_json, digest, positive, require, seal


def _source_status(plan, geometry):
    reasons = []
    if plan['partType'].get('route') != 'FLAT_PLATE':
        reasons.append('NOT_FLAT_PLATE')
    if plan.get('requiredViews', ['main']) != ['main'] or plan.get('sectionRequirements'):
        reasons.append('ADDITIONAL_VIEWS_REQUIRED')
    if reasons:
        return 'OUT_OF_SCOPE', reasons
    if (plan['status'] != 'READY' or geometry['geometryStatus'] != 'EVIDENCE_MATCHED'
            or geometry['evidenceSufficiency'] != 'SUFFICIENT'
            or geometry.get('materialFinalState', {}).get('status') != 'MATCHED'):
        return 'INPUT_BLOCKED', ['UPSTREAM_EVIDENCE_INSUFFICIENT']
    require(bool(plan['requirements']), 'REQUIREMENTS_EMPTY', str(geometry['id']))
    return 'SOURCE_READY', []


def freeze_native_sources(evidence, model_tolerance_mm=0.1):
    """A source envelope is not LayoutInput, PLAN_VALID or a write authorization.

    Recompute upstream gates; never trust READY flags in a supplied plan. NC
    capture wrappers require a separate adapter and are deliberately rejected.
    """
    canonical_json(evidence)
    require(evidence.get('schemaVersion') == 'teklaNativeEvidence.v1',
            'SOURCE_SCHEMA', 'Stage 0 accepts native evidence only')
    positive(model_tolerance_mm, 'modelToleranceMm')
    raw_parts = evidence.get('parts')
    require(isinstance(raw_parts, list) and bool(raw_parts), 'PARTS_MISSING', 'No native parts')
    ids = [str(p.get('id', '')) for p in raw_parts]
    require(all(ids) and len(ids) == len(set(ids)), 'PART_ID_INVALID', 'Missing or duplicate part IDs')
    batch = build_native_expression_requirements(evidence, model_tolerance_mm=model_tolerance_mm)
    audit = batch['geometryAudit']
    geometries = {str(p['id']): p for p in audit['parts']}
    plans = {p['source']['partId']: p for p in batch['parts']}
    require(set(plans) == set(ids) == set(geometries), 'PART_SET_MISMATCH', 'Audit lost or added parts')
    model_identity = digest(evidence['model']['value'])
    entries = []
    for raw in sorted(raw_parts, key=lambda p: str(p['id'])):
        identity = str(raw['id'])
        plan, geometry = plans[identity], geometries[identity]
        source = plan['source']
        require(source['nativeInputDigest'] == audit['inputDigest'] and
                source['modelIdentifier'] == model_identity and
                source['partPosition'] == geometry['partPosition'],
                'SOURCE_MISMATCH', identity)
        requirements = plan['requirements']
        req_ids = [r['id'] for r in requirements]
        require(len(req_ids) == len(set(req_ids)), 'REQUIREMENT_ID_DUPLICATE', identity)
        views = plan.get('requiredViews', ['main'])
        status, reasons = _source_status(plan, geometry)
        entries.append(seal({
            'schemaVersion': 'NativeLayoutSource.v1', 'status': status, 'reasons': reasons,
            'source': {**source, 'sourceDigest': plan['sourceDigest'],
                       'requirementsDigest': digest(plan)},
            'rawPart': raw, 'geometry': geometry, 'requirements': plan,
            'units': 'MODEL_MM', 'requiredViews': views,
            'remainingGates': ['DIMENSION_ADAPTER', 'TEMPLATE_CAPABILITIES', 'LAYOUT',
                               'NATIVE_READBACK', 'VISUAL_QA'],
        }))
    return seal({'schemaVersion': 'NativeLayoutSources.v1', 'nativeInputDigest': audit['inputDigest'],
                 'auditVersion': audit['algorithmVersion'], 'modelToleranceMm': model_tolerance_mm,
                 'entries': entries, 'counts': {status: sum(e['status'] == status for e in entries)
                     for status in ('SOURCE_READY', 'OUT_OF_SCOPE', 'INPUT_BLOCKED')},
                 'scope': 'OFFLINE_SOURCE_GATE_ONLY'})
