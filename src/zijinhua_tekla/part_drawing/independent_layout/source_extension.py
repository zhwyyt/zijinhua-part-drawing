"""Versioned source extension retaining and replaying the complete original gate."""

from ..native_expressions import _facts
from ..expression_requirements import _requirements, _pitch_candidates
from .contracts import digest, seal, require, canonical_json
from .material_proof import prove_material, VERSION


def extend_source(legacy, evidence, index):
    require(legacy['status']=='INPUT_BLOCKED', 'EXTENSION_SCOPE', 'Only blocked source can be extended')
    require(type(index) is int and 0<=index<len(evidence['parts']) and
            canonical_json(evidence['parts'][index])==canonical_json(legacy['rawPart']),
            'EXTENSION_PART_INDEX','Evidence path must identify the same raw instance')
    tolerance = legacy['requirements']['modelToleranceMm']
    material = prove_material(legacy['rawPart'], legacy['geometry'], f'#/parts/{index}', tolerance)
    facts = _facts(material)
    old = legacy['requirements']
    requirements, groups = _requirements(old['source']['partPosition'], material['solidCheck']['thickness'], facts, old['specifications'])
    source_digest = digest(dict(source=old['source'], material=material, rules=VERSION))
    plan = dict(old, facts=facts, requirements=requirements, groups=groups,
                pitchCandidates=_pitch_candidates(facts, groups), sourceDigest=source_digest,
                status='READY', issues=[], blockedStage=None, rulesVersion=VERSION,
                materialEvidence=dict(status='MATCHED', basis=material['basis'], sourceMappings=material['operationMappings']))
    return seal(dict(schemaVersion='NativeLayoutSource.v2', status='SOURCE_READY', reasons=[],
                     source={**old['source'], 'sourceDigest':source_digest, 'requirementsDigest':digest(plan)},
                     rawPart=legacy['rawPart'], requirements=plan, geometryProof=material,
                     legacySource=legacy, nativeEvidence=evidence, evidencePartIndex=index,
                     units='MODEL_MM', requiredViews=['main'], remainingGates=legacy['remainingGates']))


def verify_extended_source(source):
    from .sources import freeze_native_sources
    batch = freeze_native_sources(source['nativeEvidence'], source['requirements']['modelToleranceMm'])
    legacy = next(e for e in batch['entries'] if e['source']['partId']==source['source']['partId'])
    expected = extend_source(legacy, source['nativeEvidence'], source['evidencePartIndex'])
    require(canonical_json(expected)==canonical_json(source), 'EXTENDED_SOURCE_MISMATCH', 'Source proof replay differs')
