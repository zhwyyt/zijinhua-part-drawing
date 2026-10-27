"""End-to-end native drawing orchestration; all gates consume recorded evidence."""
import json
from pathlib import Path
import subprocess

from .native_expressions import build_native_expression_requirements
from .native_layout import build_layout
from .native_readback import audit_native_drawing
from .native_render import render_png
from .expression_requirements import content_digest
from .template_snapshot import build_template_snapshot
from .layout_solver import LayoutOverflow
from .nc_witness import _bytes


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _save(path,value):
    with Path(path).open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)


def _run(arguments,log):
    with Path(log).open('x',encoding='utf-8') as stream:
        completed=subprocess.run([str(a) for a in arguments],stdout=stream,stderr=subprocess.STDOUT,check=False)
    return completed.returncode


def _register(registry_path, registry, source, result, stage):
    entry = dict(id=source['id'], guid=source['guid'], sheet=result['sheet'], mark=result['mark'],
                 objectId=result['objectId'], drawingId=result['drawingId'], stage=stage)
    if max(entry['objectId'], entry['drawingId']) <= 0:
        raise ValueError('Owned drawing database identity unavailable')
    registry['parts'] = [p for p in registry['parts'] if p['id'] != source['id']]+[entry]
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = registry_path.with_suffix('.pending.json')
    _save(temporary, registry)
    temporary.replace(registry_path)


def _owned_identity(source, entry):
    if not max(entry.get('objectId', 0), entry.get('drawingId', 0)) > 0:
        raise ValueError('Legacy ownership lacks drawing database identity; explicit migration required, no catalog scan')
    source.update(ownedSheet=entry['sheet'], ownedObjectId=entry['objectId'], ownedDrawingId=entry['drawingId'])


def run_native_drawing_batch(output,ids=None,reader=None,writer=None,repo=None,ownership=None,drawing_attributes=None,
                             nc_settings_file=None):
    root=Path(repo) if repo else Path(__file__).resolve().parents[3]
    out=Path(output).resolve()
    if out.exists():raise ValueError('Choose a new batch output directory; old evidence is preserved')
    out.mkdir(parents=True)
    executables=[]
    for name,explicit in (('reader',reader),('drawing',writer)):
        if explicit:
            executable=Path(explicit).resolve()
        else:
            build=root/'tools'/('tekla2017_native_'+name)/'build.ps1'
            destination=out/(name+'-build')
            code=_run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',build,'-OutputDirectory',destination],out/(name+'-build.log'))
            if code:raise RuntimeError('Native '+name+' compilation failed; inspect batch build log')
            executable=destination/('TeklaNativeReader.exe' if name=='reader' else 'TeklaNativeDrawing.exe')
        if not executable.is_file():raise ValueError('Native executable not found: '+str(executable))
        executables.append(executable)
    reader,writer=executables
    command=[reader,'--out',out/'before.json']
    if ids:command+=['--ids',','.join(str(i) for i in ids)]
    if _run(command,out/'before.log'):raise RuntimeError('Native source acquisition failed; no drawing written')
    raw=_read(out/'before.json')
    if nc_settings_file:
        code=_run([reader,'--capture-nc','--ids',','.join(str(p['id']) for p in raw['parts']),
                   '--nc-settings-file',Path(nc_settings_file).resolve(),'--out',out/'nc-capture'],out/'nc-capture.log')
        if code:raise RuntimeError('Target NC capture failed; no drawing written')
        capture=_read(out/'nc-capture'/'capture.json')
        captured=json.loads(_bytes(capture['before']).decode('utf-8-sig'))
        if raw['parts']!=captured['parts'] or raw['model']!=captured['model']:
            raise RuntimeError('Target source changed before NC capture; no drawing written')
        batch=build_native_expression_requirements(capture,nc_tolerance_mm=.01,max_resolution_mm=.01)
    else:
        batch=build_native_expression_requirements(raw)
    registry_path=Path(ownership).resolve() if ownership else root/'outputs'/'native-drawing-ownership.json'
    registry=_read(registry_path) if registry_path.exists() else dict(schemaVersion='nativeDrawingOwnership.v1',owner='NATIVE-PLATE-R1',modelPath=raw['model']['value']['path'],parts=[])
    if registry.get('owner')!='NATIVE-PLATE-R1' or registry.get('modelPath')!=raw['model']['value']['path']:
        raise ValueError('Ownership manifest belongs to another drawing owner/model')
    _save(out/'expressions.json',batch)
    plans=[];reports=[]
    sources=[]
    for requirements,geometry,part in zip(batch['parts'],batch['geometryAudit']['parts'],raw['parts']):
        if requirements['status']!='READY':
            reports.append(dict(partId=part['id'],status='BLOCKED',
                                reason='; '.join(i['message'] for i in requirements['issues']),
                                partType=requirements.get('partType'),blockedStage=requirements.get('blockedStage')))
            continue
        source=dict(id=part['id'],position=requirements['source']['partPosition'],status='READY',
                    guid=part['guid']['value'],modification=part['modificationTime']['value']['value'],
                    frame=geometry['frame'],sourceDigest=requirements['sourceDigest'])
        if 'dstvCoordinateSystem' in part:
            source['sourceFrame'] = part['dstvCoordinateSystem']['value']
        owned=[p for p in registry['parts'] if p['id']==part['id'] and p['guid']==source['guid']]
        if len(owned)>1:raise ValueError('Duplicate ownership manifest entries')
        if owned:_owned_identity(source, owned[0])
        sources.append(source)
    prepared={}
    if sources:
        _save(out/'prepare-job.json',dict(modelPath=raw['model']['value']['path'],attributeFile=drawing_attributes,parts=sources))
        _run([writer,'--prepare',out/'prepare-job.json',out/'prepare'],out/'prepare.log')
        for source in sources:
            receipt=out/'prepare'/f"{source['id']}.created.json"
            if receipt.is_file():
                result=_read(receipt)
                if result['id']!=source['id'] or result['guid']!=source['guid']:
                    raise ValueError('Created drawing receipt source mismatch')
                _register(registry_path,registry,source,result,'INCOMPLETE')
        if not (out/'prepare'/'batch.json').is_file():
            raise RuntimeError('Template preparation refused or unavailable; inspect prepare.log. The tool never saves/closes a user drawing.')
        prepared={r['id']:r for r in _read(out/'prepare'/'batch.json')['results'] if r['status']=='TEMPLATE_PREPARED'}
        # Preparation can precede a layout failure. Keep the new owned sheet recoverable.
        for source in sources:
            if source['id'] not in prepared:
                reports.append(dict(partId=source['id'],status='BLOCKED',reason='Live template preparation failed; inspect prepare/batch.json'))
                continue
            result=prepared[source['id']]
            _register(registry_path,registry,source,result,'PREPARED')
    for requirements,geometry,part in zip(batch['parts'],batch['geometryAudit']['parts'],raw['parts']):
        if part['id'] not in prepared:continue
        try:
            template_raw=_read(out/'prepare'/f"{part['id']}.template.json")
            template=build_template_snapshot(template_raw,raw['model']['value']['path'],part['id'])
            _save(out/f"template-{part['id']}.json",template)
            plan=build_layout(requirements,geometry,part,template=template)
            if 'layout' in plan:
                _save(out/f"layout-{part['id']}.json",plan['layout'])
            if plan['status']!='READY':
                reasons=[i['code'] for i in plan['readability']['issues']]
                raise ValueError('PLAN blocked: coverage '+plan['coverage']['status']+('; readability '+','.join(reasons) if reasons else ''))
            owned=[p for p in registry['parts'] if p['id']==part['id'] and p['guid']==plan['guid']]
            if len(owned)>1:raise ValueError('Duplicate ownership manifest entries')
            if owned:_owned_identity(plan, owned[0])
            del plan['planDigest'];plan['planDigest']=content_digest(plan)
            plans.append(plan)
        except (ValueError,KeyError) as error:
            report=dict(partId=part['id'],status='BLOCKED',reason=str(error))
            if isinstance(error,LayoutOverflow):
                report['layout']=error.report
                _save(out/f"layout-{part['id']}.json",error.report)
            reports.append(report)
    _save(out/'job.json',dict(modelPath=raw['model']['value']['path'],attributeFile=drawing_attributes,parts=plans))
    if plans:
        _run([writer,'--write',out/'job.json',out/'native'],out/'native.log')
        native=_read(out/'native'/'batch.json')
        completed={r['id']:r for r in native['results']}
        for plan in plans:
            completed_part=completed.get(plan['id'],{})
            if completed_part.get('status')=='SAVED_REOPENED_EXPORTED':
                _register(registry_path,registry,plan,completed_part,'SAVED')
        wanted={int(p['source']['partId']):p for p in batch['parts']}
        for plan in plans:
            result=completed.get(plan['id'],{})
            if result.get('status')!='SAVED_REOPENED_EXPORTED':
                reports.append(dict(partId=plan['id'],status='FAIL',reason=result.get('error','Native write incomplete')));continue
            readback=_read(out/'native'/f"{plan['id']}.json")
            report=audit_native_drawing(wanted[plan['id']],plan,readback,out/'native'/f"{plan['id']}.dxf")
            report['drawing']=result
            render_png(out/'native'/f"{plan['id']}.dxf",out/'native'/f"{plan['id']}.png")
            _save(out/f"audit-{plan['id']}.json",report)
            reports.append({k:v for k,v in report.items() if k!='observations'})
    if sources:
        ids_read=[p['id'] for p in raw['parts']]
        code=_run([reader,'--ids',','.join(map(str,ids_read)),'--out',out/'after.json'],out/'after.log')
        after=_read(out/'after.json') if not code else {}
        unchanged=raw['parts']==after.get('parts') and raw['model']==after.get('model')
        if not unchanged:
            for report in reports:
                report.update(status='FAIL',sourceRecheck='CHANGED_OR_UNAVAILABLE')
        else:
            for report in reports:report['sourceRecheck']='UNCHANGED'
    result=dict(schemaVersion='nativeDrawingBatch.v1',parts=reports,passed=sum(p['status']=='PASS' for p in reports),
                failed=sum(p['status']!='PASS' for p in reports),scope='SUPPORTED_SIMPLE_PLATES; no production release')
    _save(out/'result.json',result)
    return result
