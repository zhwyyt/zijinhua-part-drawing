"""Thickness facts -> required section views -> per-view native commands."""
from .analytic_geometry import frame_axes
from .section_geometry import _order
from .expression_requirements import content_digest
from .layout_solver import note_bounds


def section_requirements(proof, frame, holes=(), tol=.1):
    if proof.get('status') != 'MATCHED':
        return []
    result = []
    axes = frame_axes(frame)
    for axis in sorted({b['alongAxis'] for b in proof['bevels']}):
        bevels = [b for b in proof['bevels'] if b['alongAxis'] == axis]
        low = max(b['extent'][0] for b in bevels); high = min(b['extent'][1] for b in bevels)
        stations = [low+(high-low)*fraction for fraction in (.1, .9, .25, .75, .5)]
        def avoids_holes(station):
            return all(abs(h['center'][axis]-station) > h['diameter']/2+
                       abs(h.get('direction', [0,0])[axis])*h.get('extension',0)/2+tol for h in holes)
        station = next((s for s in stations if avoids_holes(s)), None)
        if station is None:
            raise ValueError('SECTION_CUT_INTERSECTS_HOLES: no verified clear section station')
        points = []
        for face in proof['faces']:
            ring = face['points']
            for a, b in zip(ring, ring[1:]+ring[:1]):
                if abs(a[axis]-station) < 1e-8: points.append(a)
                if (a[axis]-station)*(b[axis]-station) < 0:
                    t = (station-a[axis])/(b[axis]-a[axis])
                    points.append([a[i]+t*(b[i]-a[i]) for i in range(3)])
        normal = [0.,0.,0.]; normal[axis] = 1
        ring = _order(points, normal, tol/100)
        if len(ring) < 3:
            raise ValueError('SECTION_INTERSECTION_EMPTY')
        # Both axis cases use thickness horizontally, transverse plate axis vertically.
        polygon = [[p[2],p[1-axis]] for p in ring]
        origin = [frame['origin'][i]+station*axes[axis][i] for i in range(3)]
        section_id = 'section:'+str(axis)
        result.append(dict(id=section_id, kind='SECTION', parentViewId='main',
                           label=chr(65+len(result)), alongAxis=axis, station=station,
                           frame=dict(origin=origin, axisX=axes[2], axisY=axes[1-axis]),
                           polygon=polygon, bevels=bevels, levels=proof['levels'],
                           required=['shape','thickness','angle','depth','land','cut_association'],
                           sourceRef=proof['sourceRef']))
    return result


def section_scheme(requirement):
    """P2 model-only machining callouts; no layout or paper offsets."""
    fmt = lambda v: f'{v:.2f}'.rstrip('0').rstrip('.')
    notes = []
    for b in requirement['bevels']:
        tip = [sum(p[1] for p in b['profile'])/2, sum(p[0] for p in b['profile'])/2]
        notes.append(dict(key=b['id'], kind='bevel', feature=b['id'], tip=tip,
                          text=f"{fmt(b['angle'])}°  d={fmt(b['depth'])}  c={fmt(b['land'])}",
                          values={k:b[k] for k in ('angle','depth','land')}))
    return dict(viewId=requirement['id'], polygon=requirement['polygon'], notes=notes,
                thickness=abs(requirement['levels'][1]-requirement['levels'][0]),
                label=requirement['label']+'-'+requirement['label'])


def required_texts(requirement):
    scheme=section_scheme(requirement)
    notes={n['key']:n for n in scheme['notes']}
    notes['section-label']=dict(text=scheme['label'],tip=None)
    notes['section-thickness']=dict(text='t='+f"{scheme['thickness']:.2f}".rstrip('0').rstrip('.'),tip=None)
    return notes


def build_section_layout(requirement, font_height, scale):
    """Bounded external callout layout; all dimensions stay in model units."""
    scheme = section_scheme(requirement); ring = scheme['polygon']
    x0,y0 = [min(p[i] for p in ring) for i in (0,1)]
    x1,y1 = [max(p[i] for p in ring) for i in (0,1)]
    height = font_height*scale; notes = []; boxes = []
    # A section is narrow: reserve an external right lane for explicit bevel data.
    for i, n in enumerate(sorted(scheme['notes'], key=lambda n:n['tip'][1])):
        y = max(n['tip'][1], boxes[-1]['bounds'][3]+2*height if boxes else n['tip'][1])
        width = note_bounds(n['text'], [0,0], height, leader=True)[2]*2
        at = [x1+3*height+width/2,y]
        notes.append(dict(n, at=at, height=font_height))
        boxes.append(dict(key=n['key'],text=n['text'],bounds=note_bounds(n['text'],at,height,leader=True)))
    for key,text,y in [('section-label',scheme['label'],y1+3*height),
                       ('section-thickness','t='+f"{scheme['thickness']:.2f}".rstrip('0').rstrip('.'),y0-3*height)]:
        at = [(x0+x1)/2,y]
        notes.append(dict(key=key,kind=key,text=text,at=at,tip=None,height=font_height))
        boxes.append(dict(key=key,text=text,bounds=note_bounds(text,at,height)))
    curves = [dict(key='section-boundary:'+str(i),kind='line',role='boundary',start=a,end=b)
              for i,(a,b) in enumerate(zip(ring,ring[1:]+ring[:1]))]
    all_boxes = [[x0,y0,x1,y1]]+[b['bounds'] for b in boxes]
    envelope = [min(b[0] for b in all_boxes)-height,min(b[1] for b in all_boxes)-height,
                max(b[2] for b in all_boxes)+height,max(b[3] for b in all_boxes)+height]
    return dict(viewId=requirement['id'],kind='SECTION',parentViewId='main',label=requirement['label'],
                frame=requirement['frame'],scale=scale,geometryMode='ANALYTIC_NATIVE_CURVES',
                dimensions=[],notes=notes,curves=curves,radii=[],bolts=[],
                layout=dict(status='SOLVED',envelope=envelope,textBoxes=boxes),
                sectionRequirement=requirement,schemeDigest=content_digest(scheme))


def _combined_paper(template, main_envelope, sections, view_scale):
    font = template['style']['fontHeight']
    envelopes = [main_envelope]+[s['layout']['envelope'] for s in sections]
    sizes = [[(e[2]-e[0])/view_scale+4*font,(e[3]-e[1])/view_scale+4*font] for e in envelopes]
    return sum(s[0] for s in sizes)+4*font*(len(sizes)-1), max(s[1] for s in sizes), sizes


def _views_fit(template, main_envelope, sections, view_scale):
    from .view_plan import build_view_plan
    width, height, _ = _combined_paper(template, main_envelope, sections, view_scale)
    try:
        build_view_plan(template, [0, 0, width, height])
        return True
    except ValueError as error:
        if str(error).startswith('LAYOUT_OVERFLOW'):
            return False
        raise


def allocate_views(template, main, sections):
    """Reserve disjoint paper rectangles. Model envelopes are divided by the shared view scale once."""
    from .view_plan import build_view_plan
    view_scale = main['scale']
    width, height, sizes = _combined_paper(template, main['layout']['envelope'], sections, view_scale)
    base = build_view_plan(template, [0, 0, width, height])
    area = base['availableArea']; x = area[0]; views = [main]+sections
    gap = 4*template['style']['fontHeight']
    for view,size in zip(views,sizes):
        view['viewPlan'] = dict(base,availableArea=[x,area[1],x+size[0],area[3]],
                                plannedEnvelope=view['layout']['envelope'],scale=view_scale)
        x += size[0]+gap
    return base


def attach_sections(plan, requirements, template):
    required = requirements.get('sectionRequirements', [])
    if not required:
        return plan
    if template is None:
        # Offline planning gets the same finite arrangement on a virtual empty sheet.
        raise ValueError('SECTION_LIVE_TEMPLATE_REQUIRED: multi-view planning needs a template snapshot')
    height = template['style']['fontHeight']
    view_scale = plan['scale']
    sections = [build_section_layout(r,height,view_scale) for r in required]
    extent = list(plan['layout']['envelope'])
    for r,s in zip(required,sections):
        axis = r['alongAxis']; cross = 1-axis
        ends=[]
        # Place marks near the material, avoiding the actual text/line scene.
        # Extending beyond the entire dimension envelope needlessly enlarged
        # a small part's view by sixteen font heights.
        from shapely.geometry import LineString, box
        from .template_snapshot import overlaps
        material = requirements['facts']['bounds']
        lines = [[c['start'],c['end']] for c in plan['curves'] if c['kind']=='line']
        for d in plan['dimensions']:
            dim_axis='xy'.index(d['axis']); dc=1-dim_axis
            line=d['coordinates'][0][dc]+d['up'][dc]*d['distance']
            pair=[list(d['coordinates'][i]) for i in (0,-1)]
            for p in pair:p[dc]=line
            lines.append(pair)
        lines += [[n['tip'],n['at']] for n in plan['notes'] if n.get('tip') is not None]
        for side in (-1,1):
            edge=material[cross+(2 if side>0 else 0)]
            for lane in range(16):
                pt=[0.,0.];pt[axis]=r['station'];pt[cross]=edge+side*(2*height+2+lane*2*height)*view_scale
                # The tag sits one font toward the part. Sideways and outward stay
                # one font, so the box can pass between bolt-hole witnesses.
                half=height*view_scale
                inward=2*height*view_scale
                bounds=[pt[0]-half,pt[1]-half,pt[0]+half,pt[1]+half]
                if side>0:
                    bounds[cross]=pt[cross]-inward
                else:
                    bounds[cross+2]=pt[cross]+inward
                rect=box(*bounds)
                grown=[min(extent[0],bounds[0]),min(extent[1],bounds[1]),max(extent[2],bounds[2]),max(extent[3],bounds[3])]
                if (any(overlaps(bounds,b['bounds']) for b in plan['layout']['textBoxes']) or
                        any(rect.intersects(LineString(line)) for line in lines) or
                        not _views_fit(template, grown, sections, view_scale)):
                    continue
                ends.append(pt)
                plan['layout']['textBoxes'].append(dict(key='section-mark:'+r['id'],text=r['label'],bounds=bounds))
                extent=grown
                break
            else:
                raise ValueError('LAYOUT_OVERFLOW: no collision-free section mark candidate')
        # Direction fixes the viewing normal; displayFrame is verified separately.
        if axis == 1: ends.reverse()
        s['cut'] = dict(start=ends[0],end=ends[1],depthUp=.1,depthDown=.1)
    plan['layout']['envelope']=extent
    allocate_views(template,plan,sections)
    plan.update(schemaVersion='nativeDrawingPlan.v2',viewId='main',sections=sections)
    plan['sectionCoverage']=audit_section_plan(requirements,plan)
    if plan['sectionCoverage']['status'] != 'PLAN_COMPLETE':
        plan['status']='BLOCKED'
    plan.pop('planDigest',None);plan['planDigest']=content_digest(plan)
    return plan


def audit_section_plan(requirements, plan):
    """P3 cannot accept omission or main-view substitution of section facts."""
    from .template_snapshot import overlaps
    errors=[]; required=requirements.get('sectionRequirements',[]); actual=plan.get('sections',[])
    if [s['id'] for s in required] != [s['viewId'] for s in actual]:
        errors.append('SECTION_VIEW_SET_MISMATCH')
    for r,s in zip(required,actual):
        if s.get('parentViewId')!='main' or s.get('frame')!=r['frame'] or s.get('scale')!=plan['scale']:
            errors.append('SECTION_FRAME_OR_PARENT_MISMATCH')
        polygon=r['polygon']; expected=[(a,b) for a,b in zip(polygon,polygon[1:]+polygon[:1])]
        if [(c['start'],c['end']) for c in s['curves']] != expected:
            errors.append('SECTION_BOUNDARY_MISMATCH')
        notes={n['key']:n for n in s['notes']}
        wanted=required_texts(r)
        if set(notes)!=set(wanted):
            errors.append('SECTION_TEXT_SET_MISMATCH')
        for key,n in wanted.items():
            if key not in notes or any(notes[key].get(k)!=n[k] for k in ('text','tip')):
                errors.append('SECTION_BEVEL_EXPRESSION_MISSING')
        for point in s.get('cut',{}).get('start',[]),s.get('cut',{}).get('end',[]):
            if len(point)!=2 or abs(point[r['alongAxis']]-r['station'])>1e-6:
                errors.append('SECTION_CUT_STATION_MISMATCH')
        if overlaps(plan['viewPlan']['availableArea'],s['viewPlan']['availableArea']):
            errors.append('SECTION_MAIN_PAPER_OVERLAP')
    for i,s in enumerate(actual):
        if any(overlaps(s['viewPlan']['availableArea'],q['viewPlan']['availableArea']) for q in actual[i+1:]):
            errors.append('SECTION_PAPER_OVERLAP')
    return dict(status='FAIL' if errors else 'PLAN_COMPLETE',errors=errors,views=[s['id'] for s in required])
