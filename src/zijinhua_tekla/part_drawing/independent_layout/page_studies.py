"""Prepare new-page calibration and audit measured, persisted text samples."""
import hashlib
import json
import math
from pathlib import Path

from .contracts import require, seal, digest
from .sources import freeze_native_sources
from .template_bundles import verify_template_bundle, check_bundle_load_binding
from .template_loads import _path


def prepare_page_job(evidence, part_id, studies, bundles, entries, load_study, ownership_path, run_id):
    """Local preparation only; never install assets or call the drawing SDK."""
    batch = freeze_native_sources(evidence)
    matches = [s for s in batch['entries'] if s['source']['partId']==str(part_id)]
    require(len(matches)==1 and matches[0]['status']=='SOURCE_READY', 'PAGE_SOURCE', 'Admitted single-view source required')
    source = matches[0]
    check_bundle_load_binding(load_study,studies)
    require(load_study['status']=='STUDY_ONLY' and all(t['status']=='ISOLATED_LOAD_MATCHED' for t in load_study['templates']),
            'PAGE_LOAD', 'All three isolated loads must match')
    require(len(bundles)==len(entries)==3 and len({b['templateId'] for b in bundles})==3,
            'PAGE_BUNDLES', 'Three distinct bundles required')
    by_id = {s['templateId']:s for s in studies}
    entry_by_id = {e['templateId']:e for e in entries}
    require(len(entry_by_id)==3 and set(entry_by_id)==set(by_id), 'PAGE_ENTRIES', 'Prepared file entries differ')
    model = evidence['model']['value']['path']
    registry = json.loads(Path(ownership_path).read_text('utf-8-sig'))
    require(registry['owner']=='NATIVE-PLATE-R1' and _path(registry['modelPath'])==_path(model),
            'PAGE_REGISTRY', 'Ownership belongs to another model')
    reserved = registry['parts']+registry.get('independentLayoutTests',[])
    first_sheet = max([int(r['sheet']) for r in reserved]+[0])+1
    templates = []
    for bundle in sorted(bundles,key=lambda b:b['templateId']):
        study,entry = by_id[bundle['templateId']],entry_by_id[bundle['templateId']]
        verify_template_bundle(bundle,study)
        require(_path(model)==_path(bundle['modelPath']), 'PAGE_MODEL', 'Different model')
        require(entry['alias']==bundle['alias'] and entry['bundleDigest']==bundle['digest'] and
                {f['name']:f['sha256'] for f in entry['files']}=={f['name']:f['sha256'] for f in bundle['files']} and
                len(entry['files'])==len(bundle['files']), 'PAGE_ASSETS', 'File manifest differs from bundle')
        for item in entry['files']:
            require(hashlib.sha256(Path(item['source']).read_bytes()).hexdigest()==item['sha256'], 'PAGE_ASSET_BYTES', item['name'])
        styles = [(role,s) for role,values in sorted(study['observedRoleStyles'].items()) for s in values]
        require(bool(styles), 'PAGE_STYLES', 'No observed styles')
        height = max(s['height'] for _,s in styles)
        width,sheet_height = study['sheetSizeNative']
        samples = []
        for role,style in styles:
            for text in (source['source']['partPosition'],'0123456789 R=35 Φ26 零件图'):
                index = len(samples)
                at = [width*.2,sheet_height*.85-index*height*3]
                require(at[1]>height*2, 'PAGE_SAMPLE_SPACE', 'Sample rows exceed sheet')
                samples.append(dict(key=f'{role}:{index}',font=style['font'],height=style['height'],text=text,at=at))
        templates.append(dict(templateId=bundle['templateId'],alias=bundle['alias'],bundleDigest=bundle['digest'],
                              files=entry['files'],size=study['sheetSizeNative'],samples=samples,sheet=first_sheet+len(templates)))
    raw = source['rawPart']
    return dict(runId=run_id,modelPath=model,partId=part_id,guid=raw['guid']['value'],
                position=source['source']['partPosition'],modified=raw['modificationTime']['value']['value'],
                sourceEnvelopeDigest=source['digest'],ownership=str(Path(ownership_path).resolve()),
                ownershipSha256=hashlib.sha256(Path(ownership_path).read_bytes()).hexdigest(),templates=templates)


def _sample_map(samples):
    require(isinstance(samples,list) and samples, 'PAGE_SAMPLES', 'Missing measured samples')
    result = {}
    for sample in samples:
        key = sample['key']
        require(key not in result and sample['objectId']>0, 'PAGE_SAMPLE_ID', key)
        numbers = [sample['height'],*sample['at'],*sample['box']]
        require(all(type(v) in (int,float) and math.isfinite(v) for v in numbers), 'PAGE_SAMPLE_FINITE', key)
        require(len(sample['at'])==2 and len(sample['box'])==4 and sample['height']>0 and
                sample['box'][0]<sample['box'][2] and sample['box'][1]<sample['box'][3], 'PAGE_SAMPLE_BOX', key)
        result[key] = sample
    require(len({s['objectId'] for s in samples})==len(samples), 'PAGE_SAMPLE_ID', 'Duplicate database object')
    return result


def audit_page_samples(planned, captures, tolerance=1e-6):
    """SDK boxes are auxiliary observations and can under-bound actual ink."""
    require(len(captures)==3, 'PAGE_REOPEN_COUNT', 'Before plus two actual reopens required')
    observed = [_sample_map(c['samples']) for c in captures]
    required = {s['key']:s for s in planned}
    require(len(required)==len(planned) and all(set(s)==set(required) for s in observed), 'PAGE_SAMPLE_SET', 'Samples missing/added')
    checks = []
    for key,expected in required.items():
        rows = [o[key] for o in observed]
        values_match = all(r['text']==expected['text'] and r['font']==expected['font'] and
                           r['height']==expected['height'] and r.get('angle',0)==expected.get('angle',0) and r['fixedPlacement'] is True and
                           all(abs(a-b)<=tolerance for a,b in zip(r['at'],expected['at'])) for r in rows)
        stable = all(r['objectId']==rows[0]['objectId'] and
                     all(abs(a-b)<=tolerance for a,b in zip(r['box'],rows[0]['box'])) for r in rows[1:])
        checks.append(dict(key=key,valuesMatch=values_match,boundsStable=stable,observations=rows))
    return dict(status='NATIVE_TEXT_BOXES_STABLE' if all(c['valuesMatch'] and c['boundsStable'] for c in checks) else 'INPUT_BLOCKED',
                toleranceNative=tolerance,samples=checks,scope='NATIVE_BOUNDING_BOX_NOT_GLYPH_PROOF')


def _texts(node):
    if isinstance(node,dict):
        if node.get('type','').endswith('.TextPrimitive'):
            yield {k:node.get(k) for k in ('Text','Font','Height','Angle','Position','Proportion')}
        for value in node.values():
            yield from _texts(value)
    elif isinstance(node,list):
        for value in node:
            yield from _texts(value)


def audit_page_content(page, source, size):
    """Observed Tekla 2017 sheet table segments; samples cannot satisfy this gate.

    ObjectType 263 is the sampled table display container, not a complete table
    dependency or frame geometry proof. Only source string occurrences are bound.
    """
    tables = []
    for view in page['presentation']:
        at = view.get('Position',{})
        if at.get('X')!=0 or at.get('Y')!=0:
            continue
        tables.extend(s for s in view.get('Primitives',[]) if s.get('ObjectType')==263)
    texts = list(_texts(tables)); values = {t['Text'] for t in texts}
    expected = {k:source.get(k) for k in ('position','profile','material')}
    matches = {k:bool(v) and v in values for k,v in expected.items()}
    matches['drawingMark'] = page['drawing']['Mark'] in values
    bounds = []
    for item in page['views']:
        v = item['view']; at,offset = v['Origin'],v['FrameOrigin']
        x,y = at['X']+offset['X'],at['Y']+offset['Y']
        box = [x,y,x+v['Width'],y+v['Height']]
        inside = all(math.isfinite(n) for n in box) and 0<=x<box[2]<=size[0] and 0<=y<box[3]<=size[1]
        bounds.append(dict(box=box,withinSheet=inside))
    return dict(status='TABLE_TEXT_MATCHED' if tables and all(matches.values()) else 'INPUT_BLOCKED',
                tableSegmentCount=len(tables),sourceStrings=expected,matches=matches,tableText=texts,
                layoutChecks=dict(singleMainView=len(bounds)==1,viewFramesWithinSheet=bool(bounds) and all(b['withinSheet'] for b in bounds)),
                viewFrames=bounds,scope='TABLE_TEXT_OCCURRENCE_NOT_FULL_CONTENT_OR_FRAME_PROOF')


def study_native_pages(job, probe, captures, ownership_before, ownership_after, view_receipts=None):
    """Audit the recorded lifecycle; report unresolved template/font scope explicitly."""
    require(probe['schemaVersion']=='NativePageProbe.v1' and probe['runId']==job['runId'] and
            probe['partId']==job['partId'] and _path(probe['modelPath'])==_path(job['modelPath']) and
            probe['sourceEnvelopeDigest']==job['sourceEnvelopeDigest'], 'PAGE_PROBE_BINDING', 'Different calibration job')
    identities = {'零件图10','零件图20','零件图50'}
    require(len(job['templates'])==3 and {t['templateId'] for t in job['templates']}==identities,
            'PAGE_TEMPLATE_SET', 'All three templates required')
    records = probe['results']
    require(len(records)==3 and {r['templateId'] for r in records}==identities and set(captures)==identities,
            'PAGE_RESULT_SET', 'Missing page result/captures')
    require(len({r['objectId'] for r in records})==3 and len({r['sheet'] for r in records})==3,
            'PAGE_RESULT_ID', 'Each template requires a distinct test page')
    # Every pre-existing registry field/entry must remain; the new partition is additive.
    old_fields = {k:v for k,v in ownership_before.items() if k!='independentLayoutTests'}
    checks = dict(executionClean=probe['error'] is None,
                  sourceStateUnchanged=probe['stateUnchanged'] is True and probe['before']==probe['after'],
                  registryBaseUnchanged=all(ownership_after.get(k)==v for k,v in old_fields.items()),
                  originalFilesUnchanged=probe['originalFilesUnchanged'] is True and all(
                      probe['finalFiles'].get(k)==v for k,v in probe['originalFiles'].items()))
    prior = ownership_before.get('independentLayoutTests',[])
    registered = ownership_after.get('independentLayoutTests',[])
    checks['priorTestsUnchanged'] = all(r in registered for r in prior)
    checks['registryPartitionExact'] = len(registered)==len(prior)+len(records) and all(r in prior+records for r in registered)
    expected_files = dict(probe['originalFiles'])
    for template in job['templates']:
        for item in template['files']:
            require(item['name'] not in expected_files, 'PAGE_ASSET_COLLISION', item['name'])
            expected_files[item['name']] = item['sha256']
    checks['assetInventoryExact'] = probe['assetsMatch'] is True and probe['finalFiles']==expected_files
    results = []
    by_id = {r['templateId']:r for r in records}
    for template in job['templates']:
        identity = template['templateId']; record = by_id[identity]; pages = captures[identity]
        require(record in registered and record['stage']=='SAVED_REOPENED_EXPORTED' and
                record['id']==job['partId'] and record['guid']==job['guid'] and record['runId']==job['runId'] and record['sheet']==template['sheet'] and
                record['bundleDigest']==template['bundleDigest'] and record['alias']==template['alias'] and
                record['objectId']>0 and record['productionEligible'] is False, 'PAGE_OWNERSHIP', identity)
        require(len(pages)==3, 'PAGE_REOPEN_COUNT', identity)
        sizes = []
        for capture in pages:
            drawing = capture['page']['drawing']
            require(drawing['Name']=='NATIVE-PLATE-R1' and drawing['SheetNumber']==record['sheet'] and
                    drawing['PartIdentifier']['ID']==job['partId'] and drawing['Mark']==record['mark'] and
                    drawing['IsLocked'] is False and drawing['IsFrozen'] is False and drawing['IsIssued'] is False,
                    'PAGE_IDENTITY', identity)
            size = drawing['Layout']['SheetSize']
            sizes.append([size['Width'],size['Height']])
        samples = audit_page_samples(template['samples'],pages)
        displayed = [list(_texts(c['page']['presentation'])) for c in pages]
        display_matches = all(all(any(t['Text']==s['text'] and t['Font']==s['font'] and t['Height']==s['height']
                                      for t in texts) for s in template['samples']) for texts in displayed)
        stable = digest(pages[1]['page'])==digest(pages[2]['page'])
        content = [audit_page_content(c['page'],probe['before']['part'],template['size']) for c in pages]
        foundation = None
        if template.get('viewControl'):
            from .foundation_studies import audit_view_control, audit_sample_masks
            require(view_receipts is not None and identity in view_receipts, 'PAGE_CONTROL_RECEIPT', identity)
            foundation = dict(view=audit_view_control(template,view_receipts[identity],pages,job['partId'],job['sourceEnvelopeDigest']),
                              masks=[audit_sample_masks(c) for c in pages])
        results.append(dict(templateId=identity,record=record,textSamples=samples,nativeContent=content,
                            foundation=foundation,
                            checks=dict(sheetSizesMatch=all(s==template['size'] for s in sizes),
                                        foundationMatched=foundation is None or (foundation['view']['status']=='VIEW_CONTROL_MATCHED' and
                                            all(m['status']=='ID_BOUND_MASKS_MATCHED' for m in foundation['masks'])),
                                        sampleDisplayStylesMatch=display_matches,
                                        consecutiveReopenSnapshotExact=stable),
                            displayText=displayed[-1]))
    complete = all(checks.values()) and all(all(r['checks'].values()) and
                   r['textSamples']['status']=='NATIVE_TEXT_BOXES_STABLE' for r in results)
    page_ready = all(c['status']=='TABLE_TEXT_MATCHED' and all(c['layoutChecks'].values()) for r in results for c in r['nativeContent'])
    return seal(dict(schemaVersion='NativePageStudy.v1',status='STUDY_ONLY' if complete and page_ready else 'INPUT_BLOCKED',
                     lifecycleStatus='TEXT_AND_REOPEN_MATCHED' if complete else 'INPUT_BLOCKED',
                     productionEligible=False,jobDigest=digest(job),probeDigest=digest(probe),checks=checks,templates=results,
                     pending=['EXACT_FONT_RESOLUTION_AND_GLYPH_OUTLINES','TABLE_AND_OTHER_ATTRIBUTE_DEPENDENCIES',
                              'MODEL_CONTENT_BINDING','SHORT_DIMENSIONS_ARROWS_AND_LEADERS',
                              'MULTISCALE_SHORTENING_AND_CLEARANCE','FULL_GEOMETRY_AND_SEMANTIC_READBACK',
                              'EXISTING_PAGE_RECOVERY_AND_INTERRUPTION'],
                     scope='NEW_TEST_PAGE_TEXT_AND_PERSISTENCE_NOT_LAYOUT_CAPABILITY'))
