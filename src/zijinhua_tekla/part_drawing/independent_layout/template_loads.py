"""Recheck detached SDK loads against immutable samples and local file snapshots."""

import hashlib
import ntpath
from pathlib import Path

from .contracts import ContractError, canonical_json, check_seal, digest, require, seal
from .templates import read_attributes


def _path(path):
    require(isinstance(path, str) and ntpath.isabs(path), 'LOAD_PATH', str(path))
    return ntpath.normcase(ntpath.normpath(path))


def _check_probe(probe, study):
    canonical_json(probe)
    check_seal(study)
    require(study['schemaVersion']=='TemplateStudy.v1' and study['status']=='STUDY_ONLY' and
            study['productionEligible'] is False, 'LOAD_STUDY', 'Expected a template sample')
    require(probe['schemaVersion']=='NativeTemplateLoadProbe.v1' and
            probe['scope']=='DETACHED_ATTRIBUTES_ONLY' and probe['mutatingApiCalls'] is False,
            'LOAD_SCOPE', 'Expected detached probe without explicit native writes')
    require(probe['stateUnchanged'] is True and probe['before']==probe['after'],
            'LOAD_STATE_CHANGED', 'Probe state differs')
    require(_path(probe['modelPath'])==_path(study['source']['modelPath'])==
            _path(probe['before']['modelPath']), 'LOAD_MODEL', 'Wrong model')
    require(probe['before']['part']['id']==probe['partId'] and probe['partId']>0,
            'LOAD_PART', 'Wrong probe target')
    require(bool(probe['assemblies']) and bool(probe['executableSha256']),
            'LOAD_RUNTIME', 'SDK and probe fingerprints required')
    identities = [entry['templateId'] for entry in probe['entries']]
    require(len(identities)==len(set(identities)) and
            set(identities)<=set(('零件图10','零件图20','零件图50')),
            'LOAD_TEMPLATE', 'Unknown or duplicate template')
    matches = [entry for entry in probe['entries'] if entry['templateId']==study['templateId']]
    require(len(matches)==1, 'LOAD_TEMPLATE', 'Missing template')
    return matches[0]


def _snapshot_files(entry, study, root):
    root = Path(root).resolve()
    if 'directoryBefore' in entry or 'directoryAfter' in entry:
        require(isinstance(entry.get('directoryBefore'),list) and
                entry['directoryBefore']==entry.get('directoryAfter'),
                'LOAD_DIRECTORY_CHANGED_DURING_PROBE', 'Attribute directory gained or lost files')
    files, snapshots, primary = {}, set(), None
    for item in entry['files']:
        source = _path(item['source'])
        require(source not in files and item['snapshot'] not in snapshots,
                'LOAD_FILE_DUPLICATE', source)
        relative = Path(item['snapshot'])
        require(not relative.is_absolute() and ':' not in item['snapshot'] and
                ntpath.basename(item['snapshot'])==item['snapshot'],
                'LOAD_SNAPSHOT_PATH', str(relative))
        path = (root / relative).resolve()
        require(path.is_relative_to(root), 'LOAD_SNAPSHOT_PATH', str(path))
        data = path.read_bytes()
        require(len(data)==item['bytes'] and hashlib.sha256(data).hexdigest()==item['sha256'],
                'LOAD_FILE_CHANGED', source)
        require(item['sha256']==item['afterSha256'], 'LOAD_FILE_CHANGED_DURING_PROBE', source)
        files[source] = item
        snapshots.add(item['snapshot'])
        if source==_path(entry['attributePath']):
            primary = data
    require(primary is not None and study.get('attributes') is not None,
            'LOAD_ATTRIBUTE_MISSING', 'Primary attribute and sampled bytes required')
    attributes = read_attributes(primary)
    require(attributes==study['attributes'], 'LOAD_ATTRIBUTE_CHANGED', 'Different sampled attribute bytes or interpretation')
    fields = {k.removeprefix('wdraw_dial.'):v for k,v in attributes['fields'].items()}
    layout = fields['dl.layout'].strip('"')
    require(bool(layout) and ntpath.basename(layout)==layout, 'LOAD_LAYOUT', 'Expected a layout name')
    primary_path = _path(entry['attributePath'])
    require(primary_path.endswith('.wd'), 'LOAD_ATTRIBUTE_PATH', 'Expected .wd')
    expected = {primary_path+suffix for suffix in ('','.more','.copt','.filter','.npfilter')}
    expected.add(_path(ntpath.join(ntpath.dirname(entry['attributePath']),layout+'.lay')))
    require(set(files)==expected, 'LOAD_FILE_SET', 'Expected attribute family and referenced layout snapshot')
    return [files[key] for key in sorted(files)]


def _drawing_matches(result, study):
    drawing = result.get('drawing')
    if result.get('drawingError') or not isinstance(drawing, dict):
        return False
    observed = study['source']['drawing']
    return (drawing.get('Name')==study['templateId'] and
            drawing.get('layout',{}).get('size')==study['sheetSizeNative'] and
            all(drawing.get(key)==observed.get(key) for key in ('Title1','Title2','Title3')))


def _rejected(result):
    drawing = result.get('drawing')
    return ((drawing is None and bool(result.get('drawingError'))) or
            (isinstance(drawing,dict) and drawing.get('Name')=='' and
             drawing.get('layout',{}).get('size')==[0,0]))


def study_template_load(probe, study, snapshot_root):
    """A successful detached read cannot certify a rendered or persisted drawing."""
    entry = _check_probe(probe, study)
    try:
        files = _snapshot_files(entry, study, snapshot_root)
    except ContractError as error:
        if error.code not in ('LOAD_FILE_CHANGED_DURING_PROBE','LOAD_DIRECTORY_CHANGED_DURING_PROBE'):
            raise
        return seal(dict(schemaVersion='TemplateLoadStudy.v1',status='INPUT_BLOCKED',
                         productionEligible=False,templateId=study['templateId'],studyDigest=study['digest'],
                         probeDigest=digest(probe),modelPath=probe['modelPath'],
                         checks=dict(filesUnchanged=False),failure=dict(code=error.code,detail=str(error)),
                         capturedEntry=entry,pending=['ATTRIBUTE_FILE_RECOVERY','ISOLATED_ATTRIBUTE_LOAD',
                             'ALL_TEMPLATE_CAPABILITY_GATES'],scope='DETACHED_LOAD_NOT_TEMPLATE_CAPABILITY'))
    stem = entry['attributePath'][:-3]
    for key in ('absolute','repeated'):
        requested = entry[key]['requested']
        require(_path(requested)==_path(stem), 'LOAD_REQUEST', key)
    for key in ('byName','repeatedName'):
        require(entry[key]['requested']==ntpath.basename(stem), 'LOAD_REQUEST', key)
    negative = entry['negative']['requested']
    require(_path(ntpath.dirname(negative))==_path(ntpath.dirname(stem)) and
            ntpath.basename(negative).startswith('__independent_missing_') and
            entry['negativeName']['requested']==ntpath.basename(negative),
            'LOAD_NEGATIVE_REQUEST', 'Missing-name controls must test both call paths')
    require(_path(negative+'.wd') not in {_path(f['source']) for f in files},
            'LOAD_NEGATIVE_REQUEST', 'Negative control references a real input')
    checks = dict(filesUnchanged=True,directoryInventoryObserved='directoryBefore' in entry,
                  nameDrawingMatches=_drawing_matches(entry['byName'],study),
                  absoluteDrawingMatches=_drawing_matches(entry['absolute'],study),
                  nameRepeatStable=entry['byName']==entry['repeatedName'],
                  absoluteRepeatStable=entry['absolute']==entry['repeated'],
                  missingNameRejected=_rejected(entry['negativeName']),
                  missingPathRejected=_rejected(entry['negative']),
                  standaloneLayoutMatches=entry['byName'].get('loadReturned') is True and
                      entry['byName'].get('layout',{}).get('size')==study['sheetSizeNative'])
    required = ('nameDrawingMatches','nameRepeatStable','absoluteRepeatStable',
                'missingNameRejected','missingPathRejected')
    pending = ['RESOLVED_ATTRIBUTE_PATH','TRANSITIVE_DEPENDENCY_CLOSURE',
               'APPLIED_DRAWING_CONTENT_AND_STYLES','UNIT_SCALE_SHORTENING_AND_PLACEMENT',
               'PRIMITIVE_GLYPH_AND_READABILITY_CALIBRATION','SAVE_REOPEN_READBACK']
    return seal(dict(schemaVersion='TemplateLoadStudy.v1',
                     status='STUDY_ONLY' if all(checks[k] for k in required) else 'INPUT_BLOCKED',
                     productionEligible=False,templateId=study['templateId'],studyDigest=study['digest'],
                     probeDigest=digest(probe),modelPath=probe['modelPath'],checks=checks,
                     files=files,observedLoads={k:entry[k] for k in
                         ('absolute','byName','repeated','repeatedName','negative','negativeName')},
                     runtime=dict(assemblies=probe['assemblies'],executableSha256=probe['executableSha256']),
                     pending=pending,scope='DETACHED_LOAD_NOT_TEMPLATE_CAPABILITY'))


def check_load_binding(load, study):
    """Supplementary diagnostics only: no preparation gate is closed by this binding."""
    check_seal(load)
    require(load['schemaVersion']=='TemplateLoadStudy.v1' and
            load['status'] in ('STUDY_ONLY','INPUT_BLOCKED') and load['productionEligible'] is False,
            'LOAD_REPORT_CONTRACT', 'Detached loads cannot assert production eligibility')
    require(load['templateId']==study['templateId'] and load['studyDigest']==study['digest'],
            'LOAD_REPORT_BINDING', 'Load report belongs to another template sample')
