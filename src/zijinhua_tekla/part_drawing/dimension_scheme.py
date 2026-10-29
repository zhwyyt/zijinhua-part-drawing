"""P2 dimension scheme: what to dimension and annotate, before paper placement.

Model units only: the same facts give the same scheme at any scale. Short
intervals are a paper-placement matter for the layout, not a selection rule.
"""
from .readability import load_profile


VERSION = 'dimension-scheme.m5'


def number(x):
    return f'{x:.1f}'.rstrip('0').rstrip('.')


def hole_callout_text(group):
    """Bolt holes use one shop form. A round hole is that form with slot extension 0."""
    if group.get('origin') == 'BOLT':
        width = group['diameter'] if group['kind'] == 'ROUND' else group['width']
        length = width if group['kind'] == 'ROUND' else group['length']
        return f"{group['count']}*D{number(width)}({number(length)}x{number(width)})"
    if group['kind'] == 'ROUND':
        return f"{group['count']}Φ{number(group['diameter'])}"
    return f"{group['count']}×D{number(group['width'])}({number(group['length'])}×{number(group['width'])})"


def assign_closed_coordinates(groups, facts, tol, profile):
    """One defining closed chain per interior station; explicit references remain.

    Hole chains retain their complete coordinates. Subsequent contour chains
    retain only new stations and both envelope extremes, avoiding extra cycles.
    """
    decisions = []
    if not profile.get('closedChains'):
        return decisions
    for axis in (0, 1):
        low, high = facts['bounds'][axis], facts['bounds'][axis+2]
        point = lambda key: facts['points'][key][axis]
        closed = [g for g in groups if g['axis'] == axis and not g.get('reference') and
                  abs(point(g['points'][0])-low) <= tol and abs(point(g['points'][-1])-high) <= tol]
        # Stable sort preserves the existing contour choice/order after hole groups.
        assigned = []
        for group in sorted(closed, key=lambda g: not any(k.startswith('hole:') for k in g['points'])):
            kept = [group['points'][0]]
            for key in group['points'][1:-1]:
                owner = next(((g, k) for g, k in assigned if abs(point(k)-point(key)) <= tol), None)
                if owner:
                    decisions.append(dict(rule='SHARED_CLOSED_CHAIN_COORDINATE', axis=axis,
                                          removedGroup=group['key'], removedPoint=key,
                                          definingGroup=owner[0], definingPoint=owner[1]))
                else:
                    kept.append(key)
                    assigned.append((group['key'], key))
            kept.append(group['points'][-1])
            if len(kept) == 2:
                groups.remove(group)
            elif kept != group['points']:
                group['points'] = kept
                group['combined'] = None
    return decisions


def hole_bands(facts, ids, axis, tol):
    """Hole clusters that share one chain along ``axis`` (2026-10-28).

    A cluster is one source feature (a bolt group, or one hole). Clusters join
    when their extents across the chain are no further apart than the plate's
    extent along it, or when they occupy the same coordinates along it. A
    witness from a farther cluster would run longer than the chain itself.
    Without source references every hole is one cluster, as before.
    """
    refs = facts.get('sourceFeatureRefs') or {}
    clusters = {}
    for k in ids:
        parent = tuple(sorted({r.split('/positions/value/')[0] for r in refs.get(k, [])})) or ('',)
        clusters.setdefault(parent if refs else ('',), []).append(k)
    clusters = list(clusters.values())
    span = facts['bounds'][axis+2]-facts['bounds'][axis]
    cross = lambda c: (min(facts['points'][k][1-axis] for k in c), max(facts['points'][k][1-axis] for k in c))
    along = lambda c: sorted({round(facts['points'][k][axis]/max(tol, 1e-9)) for k in c})
    parent = list(range(len(clusters)))

    def root(i):
        while parent[i] != i:
            i = parent[i]
        return i
    for i, a in enumerate(clusters):
        for j in range(i+1, len(clusters)):
            b = clusters[j]
            (alo, ahi), (blo, bhi) = cross(a), cross(b)
            gap = max(blo-ahi, alo-bhi, 0)
            if gap <= span+tol:
                parent[root(j)] = root(i)
    bands = {}
    for i, c in enumerate(clusters):
        bands.setdefault(root(i), []).extend(c)
    # Larger bands define shared coordinates first; ties go by position across.
    return sorted(bands.values(), key=lambda b: (-len(b), sum(facts['points'][k][1-axis] for k in b)/len(b), sorted(b)))


def build_scheme(requirements, features, profile=None):
    profile = profile or load_profile()
    facts = requirements['facts']
    bounds = facts['bounds']
    tol = requirements['modelToleranceMm']
    circular = features['circular']
    groups = []

    # Group order is the native dimension order; anchors are sorted as chains measure them.
    def chain(key, ids, axis, side, lane, role='location', combine=False):
        ids = sorted(ids, key=lambda k:(facts['points'][k][axis],k))
        if len(ids)<2: return None
        values = [abs(facts['points'][b][axis]-facts['points'][a][axis]) for a,b in zip(ids,ids[1:])]
        combined = (dict(intervalCount=len(values),pitch=values[0],text=f'{len(values)}@{number(values[0])}')
                    if combine and len(values)>1 and max(values)-min(values)<1e-6 else None)
        group = dict(key=key,axis=axis,points=ids,side=side,lane=lane,role=role,combined=combined)
        groups.append(group)
        return group

    for axis in (0,1):
        outer = [k for k in facts['points'] if k.startswith(('vertex:','edge:'))]
        low = min(outer,key=lambda k:(facts['points'][k][axis],k))
        high = max(outer,key=lambda k:(facts['points'][k][axis],k))
        side = -1 if axis==0 else 1
        found = features['axes'][axis]
        if not circular:
            overall=chain('overall-'+str(axis),[low,high],axis,side,30,'overall')
            if overall:overall['reference']=dict(category='OVERALL',reason='overall extent')
        def extreme(value, side):
            near=[k for k in outer if abs(facts['points'][k][axis]-value)<=tol]
            return max(near,key=lambda k:(side*facts['points'][k][1-axis],k))
        if not circular:
            # Convention v1 (rule A): each contour run is one chain closed edge to
            # edge. contour_chains already drops arc tangent points whose position
            # follows from R; tangent flats are not dimensioned directly.
            seen=[]
            for index, run in enumerate(found['contourChains']):
                ids, side = list(run['points']), run['side']
                if facts['points'][ids[0]][axis]>bounds[axis]+tol:ids.insert(0,extreme(bounds[axis],side))
                if facts['points'][ids[-1]][axis]<bounds[axis+2]-tol:ids.append(extreme(bounds[axis+2],side))
                values=[facts['points'][k][axis] for k in ids]
                if len(ids)==2 or any(len(v)==len(values) and all(abs(a-b)<=tol for a,b in zip(v,values)) for v in seen):
                    continue
                seen.append(values)
                chain(f'boundary-{axis}-{index}',ids,axis,side,12)
        ids=features['holeIds']
        if not ids:continue
        if features['axes'][axis]['holeDatums'] is None:continue
        bands=hole_bands(facts,ids,axis,tol)
        for band_index,band in enumerate(bands):
            # Convention v1: each band is one closed chain, envelope extreme -> each
            # distinct hole coordinate (rows/columns once) -> opposite extreme.
            middle=sum(facts['points'][k][1-axis] for k in band)/len(band)
            side=1 if middle>(bounds[1-axis]+bounds[3-axis])/2+tol else -1
            unique=[]
            for k in sorted(band,key=lambda k:(facts['points'][k][axis],-side*facts['points'][k][1-axis],k)):
                if not unique or abs(facts['points'][k][axis]-facts['points'][unique[-1]][axis])>tol:
                    unique.append(k)
            ends=[extreme(bounds[axis],side),extreme(bounds[axis+2],side)]
            anchors=[e for e in ends if all(abs(facts['points'][e][axis]-facts['points'][h][axis])>tol for h in unique)]+unique
            pitch=[abs(facts['points'][b][axis]-facts['points'][a][axis]) for a,b in zip(unique,unique[1:])]
            bolt_chain=any(h.get('origin')=='BOLT' and h['pointId'] in unique for h in facts['holes'])
            combine=(not bolt_chain and len(anchors)==len(unique) and len(pitch)>=profile['pitch']['minIntervals'])
            key=f'holes-{axis}' if len(bands)==1 else f'holes-{axis}-{band_index}'
            holes=chain(key,anchors,axis,side,12,combine=combine)
            if holes and len(bands)>1:
                # Separate bands may sit beside their own holes; the layout scores
                # that against the outside lanes (2026-10-28 proximity layout).
                holes['localAllowed']=True
                holes['band']=sorted(band)
            # User-confirmed (2026-10-21): on one side, an edge-to-edge contour chain and
            # the hole chain read as one chain, e.g. 115|55|3@80|55|15.
            same=[g for g in groups if g['axis']==axis and g['key'].startswith('boundary') and g['side']==side]
            if holes and same and len(bands)==1 and profile['chains']['mergeSameSideChains']:
                merged=[]
                for k in sorted(set(holes['points']).union(*(g['points'] for g in same)),
                                key=lambda k:(facts['points'][k][axis],not k.startswith('hole:'),k)):
                    value=facts['points'][k][axis]
                    if merged and abs(value-facts['points'][merged[-1]][axis])<=tol:
                        # Envelope extremes keep their contour vertex; elsewhere the hole wins.
                        if abs(value-bounds[axis])<=tol or abs(value-bounds[axis+2])<=tol:merged[-1]=k if not k.startswith('hole:') else merged[-1]
                        continue
                    merged.append(k)
                for g in same+[holes]:groups.remove(g)
                chain(f'holes-{axis}',merged,axis,side,12)

    decisions = assign_closed_coordinates(groups, facts, tol, profile)
    # Placement freedom is declared after expression selection. Do not let a
    # layout-side change alter merges, extreme anchors or coordinate ownership.
    for group in groups:
        movable = (group['key'].startswith(('boundary', 'holes')) and
                   group['role'] != 'overall' and 'column' not in group)
        group['allowedSides'] = [-1, 1] if movable else [group['side']]
    callouts=[]
    for g in requirements['groups']:
        text=hole_callout_text(g)
        callouts.append(dict(group=g,text=text))
    arcs=[s for s in facts['segments'] if s['kind']=='ARC']
    diameter=None
    if circular:
        diameter=dict(center=circular['center'],radius=circular['radius'],text='Φ'+number(2*circular['radius']))
    radii=[dict(segment=s,text='R='+number(s['radius'])) for s in arcs
           if not (circular and s in circular['segments'])]
    thickness=next(r['expected'] for r in requirements['requirements'] if r['id']=='plate/thickness')
    return dict(schemaVersion='dimensionScheme.v1', version=VERSION, groups=groups, decisions=decisions,
                holeCallouts=callouts, diameter=diameter, radii=radii,
                label=requirements['source']['partPosition'], thickness=thickness,
                specifications=list(requirements['specifications'].items()))
