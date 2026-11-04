"""Bind finite model-view probes to admitted source vertices and observed contours."""
import copy
import math

from .contracts import check_seal, require
from .foundation_studies import _nodes


def _vector(value):
    require(isinstance(value,(list,tuple)) and len(value)==3 and
            all(type(v) in (int,float) and math.isfinite(v) for v in value),
            'VIEW_VECTOR','Finite model vector required')
    return list(value)


def _xyz(value):
    return _vector([value[k] for k in ('X','Y','Z')])


def _dot(a,b):
    return sum(x*y for x,y in zip(a,b))


def _cross(a,b):
    return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]


def _basis(x,y):
    x,y=_vector(x),_vector(y)
    require(abs(_dot(x,x)-1)<1e-8 and abs(_dot(y,y)-1)<1e-8 and abs(_dot(x,y))<1e-8,
            'VIEW_BASIS','Unit orthogonal axes required')
    return x,y,_cross(x,y)


def model_point(source, point):
    frame=source['requirements']['partType']['frame'];x,y,_=source_basis(source)
    return [frame['origin'][i]+x[i]*point[0]+y[i]*point[1] for i in range(3)]


def source_basis(source):
    """Tekla model axes may encode edge lengths; view-mm coordinates use unit axes."""
    frame=source['requirements']['partType']['frame']
    axes=[]
    for key in ('axisX','axisY'):
        axis=_vector(frame[key]);length=math.sqrt(_dot(axis,axis))
        require(length>1e-12,'VIEW_BASIS','Zero source direction')
        axes.append([v/length for v in axis])
    return _basis(*axes)


def bind_model_anchors(source, sample, refs):
    check_seal(source)
    require(source['status']=='SOURCE_READY' and source['units']=='MODEL_MM','VIEW_SOURCE','Admitted model source required')
    require(set(refs)==({'start','end'} if sample['kind']=='dimension' else {'tip'}),'VIEW_ANCHORS','Exact anchor roles required')
    result=copy.deepcopy(sample)
    for role,ref in refs.items():
        require(ref in source['requirements']['facts']['points'],'VIEW_ANCHORS','Missing source vertex')
        result[role]=model_point(source,source['requirements']['facts']['points'][ref])
    result.update(space='MODEL_GLOBAL',sourceDigest=source['digest'],anchorRefs=dict(refs))
    return result


def _translate(node, origin):
    result=copy.deepcopy(node)
    def visit(value):
        if isinstance(value,dict):
            for k,v in value.items():
                if k in ('Position','StartPoint','EndPoint','CenterPoint') and isinstance(v,dict) and 'X' in v and 'Y' in v:
                    v['X']+=origin[0];v['Y']+=origin[1]
                else:visit(v)
        elif isinstance(value,list):
            for v in value:visit(v)
    visit(result)
    return result


def view_context(planned, capture, row, source):
    require(source is not None,'VIEW_SOURCE','Model probes require source envelope')
    bound=bind_model_anchors(source,planned,planned.get('anchorRefs',{}))
    require(planned.get('sourceDigest')==source['digest'],'VIEW_SOURCE','Source digest mismatch')
    for role in bound['anchorRefs']:
        require(math.dist(_vector(planned[role]),bound[role])<=1e-6,'VIEW_ANCHORS','Requested anchor differs from source')
    binding=row['viewBinding'];view=binding['view'];attributes=view['Attributes']
    require(binding['modelPartId']==source['rawPart']['id'] and binding['viewId']>0 and binding['targetObjectId']>0,
            'VIEW_IDENTITY','Wrong model/view binding')
    scale=attributes['Scale'];origin=_xyz(view['Origin'])[:2]
    require(type(scale) in (int,float) and scale in (1,2,2.5,5,10,20,50) and
            attributes['FixedViewPlacing'] is True and attributes['Shortening']['CutParts'] is False,
            'VIEW_SCALE','Unsupported scale/placement/shortening')
    views=capture['page']['views']
    require(len(views)==1 and views[0]['view']['ViewType']=='FrontView' and
            all(views[0]['view'][k]==view[k] for k in ('Origin','Attributes','DisplayCoordinateSystem','ViewCoordinateSystem')),
            'VIEW_IDENTITY','Primitive view does not match actual page view')
    cs=view['DisplayCoordinateSystem'];axes=_basis(_xyz(cs['AxisX']),_xyz(cs['AxisY']));base=_xyz(cs['Origin'])
    frame=source['requirements']['partType']['frame'];normal=_basis(frame['axisX'],frame['axisY'])[2]
    require(abs(abs(_dot(normal,axes[2]))-1)<1e-8,'VIEW_PLANE','Source face is not parallel to view')
    def project(p):
        delta=[v-b for v,b in zip(_vector(p),base)]
        return [_dot(delta,axis) for axis in axes]
    projected=copy.deepcopy(planned)
    for key in ('start','end','at','tip'):
        if key in projected:projected[key]=project(planned[key])[:2]
    if 'elbows' in projected:projected['elbows']=[project(p)[:2] for p in planned['elbows']]
    if 'up' in projected:
        up=_vector(planned['up']);require(abs(_dot(up,axes[2]))<1e-8,'VIEW_PLANE','Dimension direction leaves view')
        projected['up']=[_dot(up,axis) for axis in axes[:2]]
        projected['distance']=planned['distance']*scale
    displays=[v for v in capture['page']['presentation'] if
              math.dist([v['Position']['X'],v['Position']['Y']],origin)<1e-6]
    require(len(displays)==1,'VIEW_PRESENTATION','Missing/ambiguous presentation view')
    segments=[_translate(s,origin) for s in displays[0]['Primitives']]
    parts=[s for s in segments if s.get('Id')==binding['targetObjectId']]
    require(len(parts)==1,'VIEW_CONTOUR','Missing target part presentation')
    vertices=[[line[k]['X'],line[k]['Y']] for line in _nodes(parts[0],'.LinePrimitive') for k in ('StartPoint','EndPoint')]
    require(bool(vertices),'VIEW_CONTOUR','No actual contour vertices')
    observations=[]
    for role,ref in planned['anchorRefs'].items():
        q=project(planned[role]);sheet=[q[i]/scale+origin[i] for i in range(2)]
        actual=min(vertices,key=lambda v:math.dist(v,sheet));residual=math.dist(actual,sheet)
        require(residual<=.1,'VIEW_CONTOUR','Projected anchor misses actual target contour')
        observations.append(dict(role=role,sourceRef=ref,modelPoint=planned[role],viewPoint=q,
                                 sheetPoint=sheet,actualContourVertex=actual,residualNative=residual))
    return dict(projected=projected,scale=scale,origin=origin,segments=segments,
                evidence=dict(sourceDigest=source['digest'],viewId=binding['viewId'],modelPartId=binding['modelPartId'],
                              targetObjectId=binding['targetObjectId'],scale=scale,origin=origin,anchors=observations))
