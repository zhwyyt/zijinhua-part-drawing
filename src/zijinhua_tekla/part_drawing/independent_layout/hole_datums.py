"""Real local straight-edge datums for declared hole-band dimensions."""
import copy

def local_hole_datums(plan, scheme):
    result=copy.deepcopy(scheme);facts=plan['facts'];points=facts['points'];tol=plan['modelToleranceMm']
    evidence=[]
    for group in result['groups']:
        if not group.get('localAllowed') or not group.get('band'):continue
        axis=group['axis'];cross=1-axis;band=[points[k] for k in group['band']]
        ends=[];edges=[]
        for side in (-1,1):
            candidates=[]
            limit=min(p[axis] for p in band) if side<0 else max(p[axis] for p in band)
            for edge in facts['segments']:
                if edge['kind']!='LINE' or abs(edge['start'][axis]-edge['end'][axis])>tol:continue
                low,high=sorted([edge['start'][cross],edge['end'][cross]])
                value=(edge['start'][axis]+edge['end'][axis])/2
                if low>min(p[cross] for p in band)+tol or high<max(p[cross] for p in band)-tol:continue
                if side*(value-limit)<=tol:continue
                center=sum(p[cross] for p in band)/len(band)
                endpoint=min((edge['startPoint'],edge['endPoint']),key=lambda k:(abs(points[k][cross]-center),k))
                candidates.append((abs(value-limit),edge['id'],endpoint))
            if candidates:
                _,edge,endpoint=min(candidates);ends.append(endpoint);edges.append(edge)
        if len(ends)!=2:continue
        # Existing interior stations carry the machining chain; only replace its envelope anchors.
        interior=[k for k in group['points'] if points[ends[0]][axis]+tol<points[k][axis]<points[ends[1]][axis]-tol]
        if not all(k in interior or any(abs(points[k][axis]-points[q][axis])<=tol for q in interior)
                   for k in group['band']):continue
        group['points']=[ends[0],*interior,ends[1]]
        evidence.append(dict(key=group['key'],axis=axis,edges=edges,anchors=ends,band=group['band']))
    result['localHoleDatums']=evidence
    return result
