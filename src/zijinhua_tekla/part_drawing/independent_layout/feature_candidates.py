"""Finite local hole dimensions and source-bound note candidates."""
import math

from shapely.geometry import LineString, box

from ..native_render import segment_path
from .feature_geometry import hole_shape


def near_wall(hole, center, target):
    direction=[target[i]-center[i] for i in (0,1)];distance=math.hypot(*direction)
    if hole['kind']=='ROUND':
        return [center[i]+direction[i]*hole['diameter']/2/distance for i in (0,1)]
    # Intersect the ray with the capsule; polygon error below .001 mm at this resolution.
    hit=LineString([center,target]).intersection(hole_shape(hole,center).boundary)
    return [hit.x,hit.y] if hit.geom_type=='Point' else None


def note_candidates(obj, facts, polygon, scale, height, width):
    anchors=[]
    if obj['kind']=='RADIUS':
        points=segment_path(obj['geometry']);anchors=[(None,points[len(points)//2])]
    else:
        holes={h['id']:h for h in facts['holes']}
        for key in obj['representativeCandidates']:
            h=holes[key];anchors.append((h,facts['points'][h['pointId']]))
    result=[]
    shapes={h['id']:hole_shape(h,facts['points'][h['pointId']]) for h in facts['holes']}
    for hole,anchor in anchors:
        for radius in (3,5,8,12):
            for angle in range(0,360,45):
                a=math.radians(angle)
                cx=anchor[0]/scale+math.cos(a)*(width/2+radius*height)
                cy=anchor[1]/scale+math.sin(a)*radius*height
                text=box(cx-width/2,cy-height,cx+width/2,cy+height)
                target=[cx*scale,cy*scale]
                tip=anchor
                if hole and obj['anchorPolicy']=='NEAR_BOUNDARY':
                    tip=near_wall(hole,anchor,target)
                    if tip is None:continue
                # Native LeaderLinePlacing centers text on InsertionPoint even
                # when TextAttributes.Alignment is Left (225 PDF/readback).
                end=[cx-width/2 if tip[0]/scale<cx else cx+width/2,cy]
                leader=LineString([[v/scale for v in tip],end])
                model_leader=LineString([tip,[v*scale for v in end]])
                if any(key!=(hole or {}).get('id') and model_leader.intersects(shape)
                       for key,shape in shapes.items()):continue
                # A radius leader must not cross material after touching its arc.
                if obj['kind']=='RADIUS' and leader.intersection(polygon).length>height*.1:continue
                text_clear=text.distance(polygon.boundary)>height*.3
                if not text_clear:continue
                inside=polygon.covers(text)
                if not inside and text.intersects(polygon):continue
                strokes=leader.buffer(height*.2)  # Include the native arrow around its tip.
                result.append(dict(key=obj['objectId'],text=text,lines=strokes,
                                   extent=text.union(strokes).bounds,at=[cx,cy],
                                   tip=tip,featureId=(hole or {}).get('id',obj.get('featureId')),
                                   cost=int(leader.length)+(0 if inside else 500)))
    return sorted(result,key=lambda c:(c['cost'],c['at'],c['featureId']))[:80]


def outside_labels(obj, bounds, height, width):
    x0,y0,x1,y1=bounds;result=[]
    for side in (-1,1):
        for level in (1,2,3):
            y=(y0 if side<0 else y1)+side*height*(2+3*level)
            x=(x0+x1)/2
            footprint=box(x-width/2,y-height,x+width/2,y+height)
            result.append(dict(key=obj['objectId'],text=footprint,lines=LineString(),extent=footprint.bounds,
                               at=[x,y],cost=10000+level*100))
    return result
