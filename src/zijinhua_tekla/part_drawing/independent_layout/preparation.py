"""Join semantic contracts to template studies and expose concrete remaining gates."""

from .contracts import check_seal, require, seal
from .semantic_coverage import audit_semantics
from .template_loads import check_load_binding
from .template_bundles import check_bundle_load_binding


def prepare_layout_input(source, expressions, studies, load_studies=(), bundle_load_study=None):
    """A preparation report is deliberately not an executable LayoutInput."""
    check_seal(expressions)
    coverage = audit_semantics(source, expressions)
    require(coverage['status']=='SEMANTICS_COMPLETE', 'SEMANTIC_GAP', 'Complete machining semantics required')
    require(bool(studies), 'TEMPLATE_STUDIES_MISSING', 'Supply the intended template samples')
    templates, seen = [], set()
    for study in studies:
        check_seal(study)
        require(study['schemaVersion']=='TemplateStudy.v1' and study['status']=='STUDY_ONLY' and
                study['productionEligible'] is False, 'TEMPLATE_STUDY_CONTRACT', 'A sample cannot assert production eligibility')
        identity = study['templateId']
        require(identity not in seen, 'TEMPLATE_DUPLICATE', identity)
        seen.add(identity)
        templates.append(dict(templateId=identity, studyDigest=study['digest'],
                              sheetSizeNative=study['sheetSizeNative'], pending=study['pending']))
    loads = {}
    by_id = {s['templateId']:s for s in studies}
    for load in load_studies:
        identity = load['templateId']
        require(identity in by_id and identity not in loads, 'LOAD_REPORT_TEMPLATE',
                'Unknown or duplicate load report')
        check_load_binding(load,by_id[identity])
        loads[identity] = load
    objects = expressions['objects']
    holes = [o['objectId'] for o in objects if o['kind']=='HOLE']
    contents = [o['objectId'] for o in objects if o['kind'] in ('THICKNESS','SPECIFICATION','LABEL')]
    gates = [dict(gate='TEMPLATE_LOAD_AND_DEPENDENCIES',templateIds=sorted(seen)),
             dict(gate='TEMPLATE_CONTENT_AND_OCCUPANCY',objectIds=contents),
             dict(gate='UNIT_SCALE_SHORTENING_AND_PLACEMENT',templateIds=sorted(seen)),
             dict(gate='PRIMITIVE_AND_GLYPH_CALIBRATION',objectIds=[o['objectId'] for o in objects]),
             dict(gate='NUMERIC_CLEARANCE_AND_READABILITY_PROFILE',templateIds=sorted(seen)),
             dict(gate='NATIVE_SAVE_REOPEN_READBACK',objectIds=[o['objectId'] for o in objects])]
    if holes:
        gates.append(dict(gate='HOLE_SYMBOL_OR_EXPLICIT_GEOMETRY',objectIds=holes))
    result = dict(schemaVersion='LayoutPreparation.v1',status='INPUT_BLOCKED',
                     sourceEnvelopeDigest=source['digest'],expressionDigest=expressions['digest'],
                     source=source['source'],semantics=coverage,templateStudies=templates,
                     requiredObjectIds=[o['objectId'] for o in objects],
                     requiredPrimitiveKinds=sorted({o['kind'] for o in objects}),
                     pendingGates=gates,candidateGenerationAllowed=False,nativeWriteAllowed=False,
                     scope='INPUT_PREPARATION_NOT_LAYOUT_INPUT')
    if loads:
        result['templateLoadStudies'] = [loads[key] for key in sorted(loads)]
    if bundle_load_study is not None:
        check_bundle_load_binding(bundle_load_study,studies)
        result['templateBundleLoadStudy'] = bundle_load_study
    return seal(result)
