"""Shape-independent locality obligations for independent internal dimensions."""
from ..native_render import segment_path
from .contracts import require
from .feature_geometry import hole_shape


def internal_feature_localities(expressions):
    """Bind audited dimension anchors to one internal feature, never a cost hint."""
    facts=expressions['expressionRequirements']['facts']
    segments={s['id']:s for s in facts['segments']}
    features=[]
    for hole in facts['holes']:
        features.append((hole['id'],{hole['pointId']},
                         hole_shape(hole,facts['points'][hole['pointId']]).bounds))
    for loop in facts['boundaryLoops'][1:]:
        ring=[segments[k] for k in loop['segments']]
        refs={s[k] for s in ring for k in ('startPoint','endPoint')}
        points=[p for s in ring for p in segment_path(s)]
        bounds=[min(p[0] for p in points),min(p[1] for p in points),
                max(p[0] for p in points),max(p[1] for p in points)]
        features.append((loop['id'],refs,bounds))
    groups={g['key']:g for g in expressions['scheme']['groups']}
    result={}
    for obj in expressions['objects']:
        if obj['kind']!='DIMENSION' or obj['role']=='overall':continue
        # A band can contain several holes sharing the same projected station.
        refs=set(obj['points'])|set(obj['band'])
        owners=[f for f in features if refs & f[1]]
        evidence=groups[obj['schemeKey']].get('cutoutEvidence')
        if evidence:
            require(len(owners)==1 and owners[0][0]==evidence['loopId'],
                    'LOCAL_FEATURE_BINDING','Wrong internal loop: '+obj['objectId'])
        if len(owners)==1:
            identity,_,bounds=owners[0]
            result[obj['objectId']]=dict(featureId=identity,bounds=list(bounds))
    return result


def check_local_feature_placements(expressions, placements, scale, height):
    rows={r['key']:r for r in placements}
    objects={o['objectId']:o for o in expressions['objects']}
    for key,feature in internal_feature_localities(expressions).items():
        cross=1-'xy'.index(objects[key]['axis'])
        low,high=[feature['bounds'][i]/scale for i in (cross,cross+2)]
        require(key in rows and low-height*8-1e-6<=rows[key]['ordinate']<=high+height*8+1e-6,
                'LOCAL_FEATURE_PLACEMENT','Independent internal dimension moved away from '+feature['featureId']+': '+key)
