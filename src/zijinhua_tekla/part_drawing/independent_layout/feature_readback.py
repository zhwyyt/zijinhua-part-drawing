"""Verify feature geometry and note anchors from actual saved native objects."""
import math

from shapely.geometry import Point

from .contracts import digest, require
from .feature_geometry import hole_shape, native_curves
from .foundation_studies import _nodes


def verify_note(obj, row, planned, source, project):
    data=row['data'];xy=lambda p:[p[k] for k in ('X','Y')]
    require(data['TextString']==obj['text'] and
            math.dist(xy(data['InsertionPoint']),project(planned['at']))<=1e-6 and len(row['children'])==1,
            'FEATURE_NOTE','Wrong text/position/leader count')
    leader=row['children'][0]['data']
    require(math.dist(xy(leader['StartPoint']),project(planned['tip']))<=1e-6 and not leader['ElbowPoints'],
            'FEATURE_NOTE_ANCHOR','Native leader differs')
    frame=source['requirements']['partType']['frame']
    from .view_primitives import source_basis
    axes=source_basis(source)
    tip=[sum((p-b)*u for p,b,u in zip(planned['tip'],frame['origin'],a)) for a in axes[:2]]
    if obj['kind']=='RADIUS':
        s=obj['geometry'];from ..native_render import segment_path
        points=segment_path(s);wanted=points[len(points)//2]
        require(math.dist(tip,wanted)<=1e-6,'FEATURE_NOTE_ANCHOR','Radius tip outside source arc')
    else:
        facts=source['requirements']['facts'];feature=planned['anchorRefs']['tip']
        require(feature in obj['representativeCandidates'],'FEATURE_NOTE_ANCHOR','Wrong representative')
        h=next(h for h in facts['holes'] if h['id']==feature);center=facts['points'][h['pointId']]
        if obj['anchorPolicy']=='BOLT_CENTER':
            require(math.dist(tip,center)<=1e-6,'FEATURE_NOTE_ANCHOR','Wrong bolt center')
        else:
            require(hole_shape(h,center).boundary.distance(Point(tip))<=.01,
                    'FEATURE_NOTE_ANCHOR','Tip outside real hole boundary')


def verify_curves(source, plan, captures, project, segments):
    expected=native_curves(source,plan['scale'],plan['height'])
    require(digest(plan['curves'])==digest(expected),'FEATURE_CURVE_SOURCE','Requested geometry differs from source')
    require(captures[1]['curves']==captures[2]['curves'],'FEATURE_CURVE_PERSISTENCE','Curves changed after reopen')
    xy=lambda p:[p[k] for k in ('X','Y')]
    for capture in captures:
        rows={r['key']:r for r in capture['curves']}
        require(set(rows)=={c['key'] for c in expected} and len(rows)==len(capture['curves']),
                'FEATURE_CURVE_COVERAGE','Missing/extra source curve')
        for c in expected:
            row=rows[c['key']];data=row['data']
            require(row['feature']==c['feature'] and data['type'].endswith('.'+c['kind'].capitalize()),
                    'FEATURE_CURVE_SOURCE','Wrong curve identity/type')
            for field,prop in [('start','StartPoint'),('end','EndPoint'),('center','CenterPoint')]:
                if field in c and not (field=='center' and c['kind']=='arc'):
                    require(math.dist(xy(data[prop]),project(c[field]))<=1e-6,
                            'FEATURE_CURVE_ANCHOR',c['key']+'/'+field)
            if c['kind']=='circle':
                require(abs(data['Radius']-c['radius'])<=1e-6,'FEATURE_CURVE_RADIUS',c['key'])
            if c['kind']=='arc':
                require(abs(data['Radius']-math.dist(c['start'],c['center']))<=1e-6,
                        'FEATURE_CURVE_RADIUS',c['key'])
            require(data['Attributes']['Line']['Color']=='Black','FEATURE_CURVE_STYLE',c['key'])
        objects=[o['data'] for o in capture['page']['views'][0]['objects']]
        parts=[o for o in objects if o['type'].endswith('.Part')]
        require(len(parts)==1 and parts[0]['Attributes']['VisibleLines']['Color']=='Invisible' and
                parts[0]['Attributes']['HiddenLines']['Color']=='Invisible',
                'FEATURE_DUPLICATE_CONTOUR','Source part graphics not suppressed')
        for bolt in (o for o in objects if o['type'].endswith('.Bolt')):
            a=bolt['Attributes']
            require(bolt['Hideable']['IsHidden'] and a['Color']=='Invisible' and a['Representation']=='Symbol' and
                    not a['SymbolContainsHole'] and not a['SymbolContainsAxis'],
                    'FEATURE_BOLT_GRAPHICS','Duplicate hardware/symbol remains')
    for row,c in ((next(r for r in captures[-1]['curves'] if r['key']==c['key']),c) for c in expected):
        display=[s for s in segments if s.get('Id')==row['objectId']]
        require(len(display)==1 and display[0].get('Primitives'),
                'FEATURE_CURVE_DISPLAY','Missing actual curve display: '+c['key'])
        require(display[0].get('Pen',{}).get('LineType')==(4 if c.get('centerLine') else 1),
                'FEATURE_CURVE_STYLE','Actual solid/center line type differs: '+c['key'])
        view=captures[-1]['page']['views'][0]['view'];origin=xy(view['Origin']);scale=plan['scale']
        sheet=lambda p:[v/scale+origin[i] for i,v in enumerate(project(p))]
        if c['kind']=='line':
            wanted=[sheet(c[k]) for k in ('start','end')]
            lines=list(_nodes(display[0],'.LinePrimitive'))
            require(any(min(max(math.dist(xy(l['StartPoint']),wanted[0]),math.dist(xy(l['EndPoint']),wanted[1])),
                            max(math.dist(xy(l['EndPoint']),wanted[0]),math.dist(xy(l['StartPoint']),wanted[1])))<=1e-6
                        for l in lines),'FEATURE_CURVE_DISPLAY','Line not actually displayed')
        else:
            arcs=list(_nodes(display[0],'.ArcPrimitive'));sweeps=[]
            require(len(arcs)==(2 if c['kind']=='circle' else 1),'FEATURE_CURVE_DISPLAY','Arc/circle missing')
            for arc in arcs:
                a,b=xy(arc['StartPoint']),xy(arc['EndPoint']);bulge=arc['Bulge'];chord=math.dist(a,b)
                require(abs(bulge)>1e-9 and chord>0,'FEATURE_CURVE_DISPLAY','Degenerate displayed arc')
                center=[(a[i]+b[i])/2+(-(b[1]-a[1]),b[0]-a[0])[i]*(1-4*bulge**2)/(8*bulge)
                        for i in (0,1)]
                radius=chord*(1+4*bulge**2)/(8*abs(bulge));sweeps.append(4*math.atan(2*bulge))
                wanted_radius=c.get('radius',math.dist(c.get('start',c['center']),c['center']))/scale
                require(math.dist(center,sheet(c['center']))<=1e-5 and abs(radius-wanted_radius)<=1e-5,
                        'FEATURE_CURVE_DISPLAY','Actual center/radius differs')
            if c['kind']=='arc':
                require(math.dist(xy(arcs[0]['StartPoint']),sheet(c['start']))<=1e-6 and
                        math.dist(xy(arcs[0]['EndPoint']),sheet(c['end']))<=1e-6 and sweeps[0]<0,
                        'FEATURE_CURVE_DISPLAY','Actual arc endpoints/direction differ')
            else:
                require(abs(abs(sum(sweeps))-math.tau)<=1e-6,'FEATURE_CURVE_DISPLAY','Circle is incomplete')
    return dict(curves=len(expected),holes=len(source['requirements']['facts']['holes']),
                sourceBound=True,persisted=True,displayIdsPresent=True)
