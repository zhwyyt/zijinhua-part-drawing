"""Independent persisted section/mark and exported geometry verification."""
import math

from shapely.geometry import LineString
from shapely.ops import unary_union

from .analytic_geometry import frame_axes, dot, sub
from .section_plan import required_texts, audit_section_plan
from .native_render import load_render


def _frame(actual, expected, tolerance):
    return (math.dist(actual['origin'],expected['origin']) <= tolerance and
            all(math.dist(a,b) <= 1e-8 for a,b in zip(frame_axes(actual),frame_axes(expected))))


def audit_sections(requirements, plan, raw, dxf_path):
    from .native_readback import visual_audit
    from .template_snapshot import overlaps
    errors=[]; results=[]; tol=requirements['modelToleranceMm']
    try:
        if audit_section_plan(requirements,plan)['status']!='PLAN_COMPLETE':
            raise ValueError('Section plan no longer covers independent requirements')
        required=requirements.get('sectionRequirements',[]); actual=raw.get('sections',[])
        if raw.get('schemaVersion')!='nativeDrawingReadback.v2' or len(required)!=len(actual):
            raise ValueError('Missing saved/reopened section views')
        lookup={s['viewId']:s for s in actual}
        if len(lookup)!=len(actual) or set(lookup)!={r['id'] for r in required}:
            raise ValueError('Duplicate/foreign section view identity')
        marks=[o for o in raw['objects'] if o['kind']=='section_mark']
        if len(marks)!=len(required):
            raise ValueError('Missing/extra section cut marks')
        paper=[]
        for view in [raw]+actual:
            low=[view['origin'][i]+view['frameOrigin'][i] for i in (0,1)]
            paper.append(low+[low[0]+view['width'],low[1]+view['height']])
        if any(overlaps(a,b) for i,a in enumerate(paper) for b in paper[i+1:]):
            raise ValueError('Saved view frames overlap')
        if not _frame(raw['displayFrame'],plan['frame'],tol):
            raise ValueError('Main frame changed; section cut association is invalid')
        main_rows={o['key']:o for o in raw['objects']}
        main_render=load_render(dxf_path,raw)
        main_lines=unary_union([LineString(line) for line in main_render['lines']])
        for r in required:
            for bevel in r['bevels']:
                axis=bevel['alongAxis']
                for index,profile in enumerate(bevel['profile']):
                    ends=[]
                    for station in bevel['extent']:
                        point=[0.,0.];point[axis]=station;point[1-axis]=profile[0];ends.append(point)
                    row=main_rows[bevel['id']+':rim:'+str(index)]
                    if (row['kind']!='line' or row['style']!='SolidLine' or
                            any(math.dist(row[k][:2],point)>tol for k,point in zip(('start','end'),ends)) or
                            not LineString(ends).difference(main_lines.buffer(tol)).is_empty):
                        raise ValueError('Main view bevel rim is missing or differs from the verified solid')
        for r,p in zip(required,plan['sections']):
            a=lookup[r['id']]
            if (a['stage']!='SAVED_REOPENED' or a['viewType']!='SectionView' or a['id']!=plan['id']
                    or a['planDigest']!=plan['planDigest'] or a['sourceDigest']!=requirements['sourceDigest']
                    or a['scale']!=1 or a['linkedTargetCount']!=1 or a['visiblePartCount']!=0
                    or a['geometryMode']!='ANALYTIC_NATIVE_CURVES'):
                raise ValueError('Section persistence, identity, type or target association mismatch')
            if not _frame(a['displayFrame'],r['frame'],tol):
                raise ValueError('Section display frame differs from required cut frame')
            # Section camera must lie on the required plane, even when display axes differ.
            normal=frame_axes(r['frame'])[2]; camera=a['viewFrame']
            if (abs(abs(dot(normal,frame_axes(camera)[2]))-1)>1e-8 or
                    abs(dot(normal,sub(camera['origin'],r['frame']['origin'])))>tol):
                raise ValueError('Native section camera does not match the cut plane')
            mark=[m for m in marks if m['key']=='section-mark:'+r['id']]
            if (len(mark)!=1 or mark[0]['label']!=r['label'] or
                    any(math.dist(mark[0][key][:2],p['cut'][key])>tol for key in ('start','end'))):
                raise ValueError('Saved cut mark name/position/direction changed')
            for end in ('start','end'):
                if not any(t['text']==r['label'] and math.dist(
                        [(t['min'][i]+t['max'][i])/2 for i in (0,1)],p['cut'][end])<2*p['viewPlan']['fontHeight']
                           for t in main_render['texts']):
                    raise ValueError('Section cut end label is absent from actual DXF')
            if any(b.get('hidden') is not True or b.get('color')!='Invisible' or b.get('representation')!='Symbol'
                   or b.get('hole') is not False or b.get('axis') is not False for b in a['sourceBolts']):
                raise ValueError('Section retains source bolt hardware')
            rows={o['key']:o for o in a['objects']}
            if len(rows)!=len(a['objects']) or set(rows)!={o['key'] for o in p['notes']+p['curves']}:
                raise ValueError('Section has missing/extra/duplicate annotations')
            render=load_render(dxf_path,a)
            actual_lines=unary_union([LineString(line) for line in render['lines']])
            for c in p['curves']:
                o=rows[c['key']]
                if (o['kind']!='line' or o['style']!='SolidLine' or
                        any(math.dist(o[k][:2],c[k])>tol for k in ('start','end'))):
                    raise ValueError('Saved section boundary differs from independently intersected solid')
                if not LineString([c['start'],c['end']]).difference(actual_lines.buffer(tol)).is_empty:
                    raise ValueError('Section boundary is absent from actual DXF')
            # Callout text is recomputed from facts, not accepted from the writer plan.
            wanted=required_texts(r)
            for n in p['notes']:
                o=rows[n['key']]; target=wanted[n['key']]
                if (o['kind']!='text' or o['text']!=target['text'] or abs(o['height']-n['height'])>.01
                        or math.dist(o['at'][:2],n['at'])>tol):
                    raise ValueError('Section machining text/placement changed')
                if n['tip'] is not None and (o['tip'] is None or math.dist(o['tip'][:2],target['tip'])>tol
                                               or o['arrow']!='FilledArrow'):
                    raise ValueError('Bevel leader moved off the measured face')
                if n['tip'] is None and (o['tip'] is not None or o['arrow']!='NoArrow'):
                    raise ValueError('Section label/thickness acquired an unexpected leader')
                if not any(t['text']==target['text'] and math.dist(
                        [(t['min'][i]+t['max'][i])/2 for i in (0,1)],o['at'][:2])<2*o['height'] for t in render['texts']):
                    raise ValueError('Section machining text is absent from actual DXF')
            visual=visual_audit(p,a,render)
            results.append(dict(viewId=r['id'],status=visual['status'],visual=visual,
                                verifiedBevels=[b['id'] for b in r['bevels']]))
            if visual['status']!='PASS': errors.append(dict(code='SECTION_VISUAL_FAILED',viewId=r['id']))
    except (ValueError,KeyError,TypeError,IndexError) as error:
        errors.append(dict(code='SECTION_READBACK_INVALID',message=str(error)))
    return dict(status='FAIL' if errors else 'PASS',errors=errors,views=results)
