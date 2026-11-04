"""Source-derived curves and material for complete flat-plate feature trials."""
import math

from shapely.geometry import LineString, Point, Polygon

from ..analytic_geometry import hole_segments
from ..native_render import segment_path
from .contracts import require
from .view_primitives import model_point


def hole_shape(hole, center):
    if hole['kind']=='ROUND':
        return Point(center).buffer(hole['diameter']/2,quad_segs=90)
    angle=math.radians(hole['angle']);ext=(hole['length']-hole['width'])/2
    ends=[[center[i]+s*ext*(math.cos(angle),math.sin(angle))[i] for i in (0,1)] for s in (-1,1)]
    return LineString(ends).buffer(hole['width']/2,quad_segs=90)


def material_polygon(facts, scale=1):
    by_id={s['id']:s for s in facts['segments']}
    outline=[p for k in facts['boundaryLoops'][0]['segments'] for p in segment_path(by_id[k])[:-1]]
    polygon=Polygon([(x/scale,y/scale) for x,y in outline])
    for hole in facts['holes']:
        shape=hole_shape(hole,facts['points'][hole['pointId']])
        polygon=polygon.difference(Polygon([(x/scale,y/scale) for x,y in shape.exterior.coords]))
    require(polygon.is_valid and polygon.geom_type=='Polygon','FEATURE_MATERIAL','Invalid material domain')
    return polygon


def source_curves(source, scale, height):
    """Each curve has one source feature; native Arc endpoints follow clockwise order."""
    facts=source['requirements']['facts'];curves=[]
    def add(key, feature, kind, **data):
        curves.append(dict(key=key,feature=feature,kind=kind,**data))
    for s in facts['segments']:
        data=dict(start=s['start'],end=s['end'])
        if s['kind']=='ARC':
            if not s['clockwise']:data['start'],data['end']=data['end'],data['start']
            data['center']=s['center']
        add('segment/'+s['id'],s['id'],s['kind'].lower(),**data)
    for h in facts['holes']:
        c=facts['points'][h['pointId']];key='hole/'+h['id']
        if h['kind']=='ROUND':
            add(key+'/circle',h['id'],'circle',center=c,radius=h['diameter']/2)
            lengths=[h['diameter']/2]*2;angle=0
        else:
            require(h['kind']=='SLOT','FEATURE_HOLE','Unsupported hole')
            angle=math.radians(h['angle']);lengths=[h['length']/2,h['width']/2]
            segments=hole_segments(c,h['width'],h['length']-h['width'],[math.cos(angle),math.sin(angle)])
            for i,s in enumerate(segments):
                data=dict(start=s['start'],end=s['end'])
                if s['kind']=='ARC':
                    if s['sweepRadians']>0:data['start'],data['end']=data['end'],data['start']
                    data['center']=s['center']
                add(key+'/'+str(i),h['id'],s['kind'].lower(),**data)
        for axis,length in enumerate(lengths):
            direction=[math.cos(angle+axis*math.pi/2),math.sin(angle+axis*math.pi/2)]
            ends=[[c[i]+sign*(length+height*.3*scale)*direction[i] for i in (0,1)] for sign in (-1,1)]
            add(key+'/axis'+str(axis),h['id'],'line',start=ends[0],end=ends[1],centerLine=True)
    return curves


def native_curves(source, scale, height):
    curves=source_curves(source,scale,height)
    return [dict(c,**{k:model_point(source,c[k]) for k in ('start','end','center') if k in c}) for c in curves]
