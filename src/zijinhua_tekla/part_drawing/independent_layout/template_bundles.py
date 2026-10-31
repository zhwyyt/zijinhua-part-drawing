"""Build auditable namespaced copies of already converted Tekla attributes."""

import base64
import hashlib
from pathlib import Path

from .contracts import check_seal, digest, require, seal
from .templates import read_attributes
from .template_loads import _drawing_matches, _rejected, _path


def _name(value):
    require(isinstance(value,str) and value not in ('','.','..') and
            not any(c in value for c in '\\/:*?"<>|\r\n\x00'), 'BUNDLE_NAME', str(value))
    return value


def _quoted(value):
    require(value.startswith('"') and value.endswith('"'), 'BUNDLE_REFERENCE', value)
    return value[1:-1]


def _fields(data):
    return read_attributes(data)['fields']


def _normalized(data):
    fields = _fields(data)
    normalized = {k.removeprefix('wdraw_dial.'):v for k,v in fields.items()}
    require(len(fields)==len(normalized), 'BUNDLE_NAMESPACE', 'Duplicate normalized field')
    return normalized


def _references(name, fields):
    """Only the observed drawing/view/general-dimension vocabulary is supported."""
    refs = []
    if name.endswith('.wd'):
        refs.append(('dl.layout',_name(_quoted(fields['dl.layout'])),'.lay',True))
        views = _quoted(fields['dv.aDrawingViews']).split(',')
        if views[-1]=='':
            views.pop()  # Tekla also writes a trailing list delimiter.
        require(len(views)%4==0 and bool(views), 'BUNDLE_VIEWS', 'Expected view quadruples')
        for i in range(0,len(views),4):
            require(views[i].isdigit() and views[i+1] in ('0','1','2') and views[i+2]=='',
                    'BUNDLE_VIEWS', 'Unsupported view declaration')
            if views[i+3] in ('当前属性','current attributes'):
                require(views[i+1]=='0', 'BUNDLE_CURRENT_VIEW', 'Enabled current attributes cannot be frozen')
            else:
                refs.append(('dv.aDrawingViews',_name(views[i+3]),'.vi',views[i+1]!='0'))
    elif name.endswith('.vi'):
        plugin = _quoted(fields['ViewDimensioning.aDimensioningPlugins']).split(',')
        require(len(plugin)==3 and plugin[:2]==['','albl_General'],
                'BUNDLE_PLUGIN', 'Unsupported dimensioning plugin')
        refs.append(('ViewDimensioning.aDimensioningPlugins',_name(plugin[2]),'.wdcd',True))
    elif name.endswith('.wdcd'):
        for key in ('aStraightDimensionsAttributeName','aAngleDimensionsAttributeName','aCheckDimensionsAttributeName'):
            refs.append((key,_name(_quoted(fields[key])),'.dim',True))
    return sorted(set(refs))


def _graph(primary, data):
    pending, visited, edges = [primary], set(), []
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        require(name in data, 'BUNDLE_DEPENDENCY_MISSING', name)
        visited.add(name)
        if name.endswith(('.wd','.vi')):
            suffixes = ('.more','.copt','.filter','.npfilter') if name.endswith('.wd') else ('.copt','.filter','.npfilter')
            pending.extend(name+suffix for suffix in suffixes)
        if not name.endswith(('.wd','.vi','.wdcd','.dim')):
            continue
        for field,stem,extension,required in _references(name,_fields(data[name])):
            target = stem+extension
            external = target not in data and not required
            edges.append(dict(source=name,field=field,target=target,stem=stem,extension=extension,external=external))
            if not external:
                pending.append(target)
    require(visited==set(data), 'BUNDLE_UNUSED_FILE', 'Unbound files in bundle')
    return sorted(edges,key=lambda e:(e['source'],e['field'],e['target']))


def _rewrite(data, replacements):
    result = []
    for raw in data.splitlines(keepends=True):
        key = raw.partition(b' ')[0].decode('ascii')
        if key not in replacements:
            result.append(raw)
            continue
        ending = b'\r\n' if raw.endswith(b'\r\n') else (b'\n' if raw.endswith(b'\n') else b'')
        result.append(key.encode('ascii')+b' '+replacements[key].encode('gb18030')+ending)
    return b''.join(result)


def _rewrite_references(fields, edges, stems):
    replacements = {}
    for edge in edges:
        key = edge['field']
        value = replacements.get(key,fields[key])
        # Exact comma-delimited tokens, never substring replacements in text/XML.
        tokens = _quoted(value).split(',')
        replacements[key] = '"'+','.join(stems.get(t,t) for t in tokens)+'"'
    return replacements


def build_template_bundle(study, attribute_name, original, converted, dependencies):
    check_seal(study)
    require(study['schemaVersion']=='TemplateStudy.v1' and study['status']=='STUDY_ONLY' and
            study['productionEligible'] is False and study['templateId'] in ('零件图10','零件图20','零件图50'),
            'BUNDLE_STUDY', 'Expected an allowed template sample')
    _name(attribute_name)
    require(attribute_name.endswith('.wd'), 'BUNDLE_ATTRIBUTE', 'Expected .wd')
    require(read_attributes(original)==study['attributes'], 'BUNDLE_SOURCE', 'Original sample changed')
    previous,current = _normalized(original),_fields(converted)
    require(not any(k.startswith('wdraw_dial.') for k in current), 'BUNDLE_LEGACY', 'Converted format required')
    require(all(current.get(k)==v for k,v in previous.items()) and
            set(current)-set(previous)<= {'dv.aDrawingViews'}, 'BUNDLE_MIGRATION', 'Existing fields changed during conversion')
    require(attribute_name not in dependencies, 'BUNDLE_DUPLICATE', attribute_name)
    data = {attribute_name:converted,**dependencies}
    require(len({n.casefold() for n in data})==len(data), 'BUNDLE_DUPLICATE', 'Case-insensitive name collision')
    for name in data:
        _name(name)
    edges = _graph(attribute_name,data)
    fingerprints = {n:hashlib.sha256(b).hexdigest() for n,b in data.items()}
    alias = 'IL_'+digest(dict(studyDigest=study['digest'],files=fingerprints))[:24]
    stems = {attribute_name[:-3]:alias}
    for stem in sorted({e['stem'] for e in edges if not e['external']}):
        require(stem not in stems, 'BUNDLE_STEM_COLLISION', stem)
        stems[stem] = alias+'_r'+str(len(stems))
    names = {}
    for name in data:
        matches = [s for s in stems if name.startswith(s+'.')]
        require(len(matches)==1, 'BUNDLE_FILE_IDENTITY', name)
        stem = matches[0]
        names[name] = stems[stem]+name[len(stem):]
    output,changes = [],[]
    for name,raw in sorted(data.items()):
        replacements = {}
        if name.endswith(('.wd','.vi','.wdcd','.dim')):
            fields = _fields(raw)
            replacements = _rewrite_references(fields,[e for e in edges if e['source']==name],stems)
            if name==attribute_name:
                for key in ('in_file','out_file'):
                    require(key in fields, 'BUNDLE_IDENTITY_FIELD', key)
                    replacements[key] = '"'+alias+'"'
            rewritten = _rewrite(raw,replacements)
            require(_fields(rewritten)==dict(fields,**replacements), 'BUNDLE_REWRITE', 'Unexpected field change')
            changes.extend(dict(file=name,field=k,before=fields[k],after=v) for k,v in sorted(replacements.items()) if fields[k]!=v)
        else:
            rewritten = raw
        output.append(dict(name=names[name],sourceName=name,sha256=hashlib.sha256(rewritten).hexdigest(),
                           contentBase64=base64.b64encode(rewritten).decode('ascii')))
    _graph(alias+'.wd',{f['name']:base64.b64decode(f['contentBase64']) for f in output})
    return seal(dict(schemaVersion='TemplateBundle.v1',status='BUNDLE_PREPARED',productionEligible=False,
                     templateId=study['templateId'],studyDigest=study['digest'],modelPath=study['source']['modelPath'],
                     alias=alias,attributeName=attribute_name,originalBase64=base64.b64encode(original).decode('ascii'),
                     sources=[dict(name=n,sha256=fingerprints[n],contentBase64=base64.b64encode(b).decode('ascii')) for n,b in sorted(data.items())],
                     files=output,referenceEdges=edges,fieldChanges=changes,
                     unresolvedDisabledViews=sorted({e['target'] for e in edges if e['external']}),
                     preservedOriginalFieldCount=len(previous),
                     pending=['BINARY_LAYOUT_TABLE_DEPENDENCIES','OTHER_ATTRIBUTE_REFERENCES','FONT_DEPENDENCIES',
                              'APPLIED_DRAWING_CONTENT_AND_STYLES','UNIT_SCALE_AND_READABILITY','SAVE_REOPEN'],
                     scope='NAMED_ATTRIBUTE_DEPENDENCIES_NOT_PRODUCTION_TEMPLATE'))


def verify_template_bundle(bundle, study):
    check_seal(bundle)
    sources = {}
    for item in bundle['sources']:
        require(item['name'] not in sources, 'BUNDLE_DUPLICATE', item['name'])
        raw = base64.b64decode(item['contentBase64'],validate=True)
        require(hashlib.sha256(raw).hexdigest()==item['sha256'], 'BUNDLE_BYTES', item['name'])
        sources[item['name']] = raw
    converted = sources.pop(bundle['attributeName'])
    expected = build_template_bundle(study,bundle['attributeName'],
                                    base64.b64decode(bundle['originalBase64'],validate=True),converted,sources)
    require(expected==bundle, 'BUNDLE_REPLAY', 'Bundle differs from source-derived copy')


def stage_bundle_files(bundle, study, directory):
    """Write local prepared artifacts only; native staging is a separate guarded tool."""
    verify_template_bundle(bundle,study)
    directory.mkdir(parents=True,exist_ok=False)
    for item in bundle['files']:
        (directory/item['name']).write_bytes(base64.b64decode(item['contentBase64'],validate=True))
    return dict(templateId=bundle['templateId'],alias=bundle['alias'],bundleDigest=bundle['digest'],
                expected=dict(Name=study['templateId'],Title1=study['source']['drawing'].get('Title1'),
                              Title2=study['source']['drawing'].get('Title2'),Title3=study['source']['drawing'].get('Title3'),
                              size=study['sheetSizeNative']),
                files=[dict(name=f['name'],source=str((directory/f['name']).resolve()),sha256=f['sha256']) for f in bundle['files']])


def study_bundle_load(probe, bundles, studies, snapshot_root):
    """Replay all package/file bindings; a detached load never closes drawing gates."""
    required = {'零件图10','零件图20','零件图50'}
    for items in (bundles,studies,probe['entries']):
        require(len(items)==3 and {v['templateId'] for v in items}==required,
                'BUNDLE_ALL_TEMPLATES', 'All three distinct templates are required')
    require(probe['schemaVersion']=='NativeTemplateBundleProbe.v1' and
            probe['scope']=='DETACHED_ISOLATED_ATTRIBUTES_ONLY' and probe['mutatingDrawingApiCalls'] is False,
            'BUNDLE_PROBE_SCOPE', 'Expected detached isolated loading')
    require(probe['partId']>0 and probe['before']['part']['id']==probe['partId'] and
            probe['assemblies'] and probe['executableSha256'], 'BUNDLE_PROBE_RUNTIME', 'Missing runtime/target')
    root = Path(snapshot_root).resolve()
    originals = probe['originalFiles']
    require(bool(originals) and len({n.casefold() for n in originals})==len(originals),
            'BUNDLE_ORIGINALS', 'Invalid directory inventory')
    for name,checksum in originals.items():
        _name(name)
        path = (root/'originals'/name).resolve()
        require(path.is_relative_to(root/'originals'), 'BUNDLE_SNAPSHOT_PATH', name)
        require(hashlib.sha256(path.read_bytes()).hexdigest()==checksum, 'BUNDLE_SNAPSHOT', name)
    by_id = {s['templateId']:s for s in studies}
    by_bundle = {b['templateId']:b for b in bundles}
    installed = dict(originals)
    for bundle in bundles:
        study = by_id[bundle['templateId']]
        verify_template_bundle(bundle,study)
        require(_path(probe['modelPath'])==_path(study['source']['modelPath'])==_path(probe['before']['modelPath']),
                'BUNDLE_MODEL', 'Different model')
        require(originals.get(bundle['attributeName'])==study['attributes']['bytesSha256'],
                'BUNDLE_ORIGINAL_BINDING', 'Original .wd differs from study')
        for item in bundle['files']:
            require(item['name'].casefold() not in {n.casefold() for n in installed},
                    'BUNDLE_ALIAS_COLLISION', item['name'])
            installed[item['name']] = item['sha256']
    checks = dict(filesRestored=probe['filesRestored'] is True and originals==probe['finalFiles'],
                  stateUnchanged=probe['stateUnchanged'] is True and probe['before']==probe['after'],
                  executionClean=probe['error'] is None and probe['cleanupErrors']==[])
    phases = [('locked',originals)]
    phases += [('absentBefore:'+e['alias'],originals) for e in probe['entries']]
    phases += [('installed',installed)]
    phases += [(key+':'+e['alias'],installed) for e in probe['entries'] for key in ('first','second')]
    phases += [('cleaned',originals)]
    phases += [('absentAfter:'+e['alias'],originals) for e in probe['entries']]
    checks['everyCallUnchanged'] = len(phases)==len(probe['events']) and all(
        e['phase']==phase and e['files']==files and e['filesMatch'] is True and
        e['state']==probe['before'] and e['stateMatch'] is True
        for e,(phase,files) in zip(probe['events'],phases))
    results = []
    for entry in probe['entries']:
        bundle,study = by_bundle[entry['templateId']],by_id[entry['templateId']]
        require(entry['alias']==bundle['alias'] and entry['bundleDigest']==bundle['digest'],
                'BUNDLE_LOAD_BINDING', 'Probe loaded another bundle')
        observed = {k:entry.get(k,{}) for k in ('absentBefore','first','second','absentAfter')}
        loads = dict(requestsMatch=all(v.get('requested')==bundle['alias'] for v in observed.values()),
                     absentBeforeRejected=_rejected(observed['absentBefore']) and observed['absentBefore'].get('drawingError') is None,
                     firstMatches=_drawing_matches(observed['first'],study),
                     secondMatches=_drawing_matches(observed['second'],study),
                     repeatStable=observed['first']==observed['second'],
                     absentAfterRejected=_rejected(observed['absentAfter']) and observed['absentAfter'].get('drawingError') is None)
        results.append(dict(templateId=entry['templateId'],bundleDigest=bundle['digest'],
                            studyDigest=study['digest'],checks=loads,observed=observed,
                            status='ISOLATED_LOAD_MATCHED' if all(loads.values()) and all(checks.values()) else 'INPUT_BLOCKED'))
    matched = all(r['status']=='ISOLATED_LOAD_MATCHED' for r in results)
    return seal(dict(schemaVersion='TemplateBundleLoadStudy.v1',status='STUDY_ONLY' if matched else 'INPUT_BLOCKED',
                     productionEligible=False,checks=checks,templates=results,probeDigest=digest(probe),
                     pending=sorted({p for b in bundles for p in b['pending']}|{'DISABLED_EXTERNAL_VIEW_DEPENDENCIES'}),
                     scope='ISOLATED_LOAD_NOT_TEMPLATE_CAPABILITY'))


def check_bundle_load_binding(report, studies):
    """Accept supplemental diagnostics only; preparation retains every native gate."""
    check_seal(report)
    require(report['schemaVersion']=='TemplateBundleLoadStudy.v1' and
            report['status'] in ('STUDY_ONLY','INPUT_BLOCKED') and report['productionEligible'] is False,
            'BUNDLE_LOAD_REPORT', 'Expected non-production isolated load study')
    expected = {s['templateId']:s['digest'] for s in studies}
    entries = report['templates']
    require(len(entries)==3 and len(expected)==3 and
            {e['templateId'] for e in entries}==set(expected)=={'零件图10','零件图20','零件图50'} and
            all(e['studyDigest']==expected[e['templateId']] for e in entries),
            'BUNDLE_LOAD_REPORT_BINDING', 'Load study belongs to different template samples')
