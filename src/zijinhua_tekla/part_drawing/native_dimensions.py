"""Choose contour feature chains and real machining datums, before paper layout."""
from .expression_requirements import axis_aligned_arc


def contour_chains(facts, axis, tolerance):
    """Outer monotone runs keep chamfers and opposite shoulders in their own chains."""
    by_id = {s['id']: s for s in facts['segments']}
    segments = [by_id[k] for k in facts['boundaryLoops'][0]['segments']]
    count = len(segments)
    area = sum(s['start'][0]*s['end'][1]-s['end'][0]*s['start'][1] for s in segments)
    orientation = 1 if area > 0 else -1
    omitted = set()
    for i, s in enumerate(segments):
        if s['kind'] != 'ARC' or not axis_aligned_arc(s, tolerance):
            continue
        # Only a line tangent in this axis makes the arc endpoint redundant.
        for point, neighbour in ((s['startPoint'], segments[(i-1)%count]),
                                 (s['endPoint'], segments[(i+1)%count])):
            if (neighbour['kind'] == 'LINE' and abs(neighbour['start'][1-axis]-neighbour['end'][1-axis]) <= tolerance
                    and abs(facts['points'][point][axis]-s['center'][axis]) <= tolerance):
                omitted.add(point)
    directed = [(i, 1 if s['end'][axis] > s['start'][axis] else -1)
                for i, s in enumerate(segments) if abs(s['end'][axis]-s['start'][axis]) > tolerance]
    if not directed:
        return []
    pivot = next((i for i in range(len(directed)) if directed[i][1] != directed[i-1][1]), 0)
    directed = directed[pivot:]+directed[:pivot]
    groups = []
    for edge in directed:
        if not groups or groups[-1][-1][1] != edge[1]:
            groups.append([])
        groups[-1].append(edge)
    result = []
    for index, group in enumerate(groups):
        start = (groups[index-1][-1][0]+1)%count
        end = groups[(index+1)%len(groups)][0][0]
        indices = [start]
        while indices[-1] != end:
            indices.append((indices[-1]+1)%count)
            if len(indices) > count+1:
                raise ValueError('Contour run is not closed')
        side = (-1 if axis == 0 else 1)*group[0][1]*orientation
        ids = [segments[i]['startPoint'] for i in indices if segments[i]['startPoint'] not in omitted]
        stations = []
        for key in sorted(ids, key=lambda k: (facts['points'][k][axis], k)):
            if not stations or abs(facts['points'][key][axis]-facts['points'][stations[-1][0]][axis]) > tolerance:
                stations.append([])
            stations[-1].append(key)
        if len(stations) < 2:
            continue
        representatives = [max(items, key=lambda k: side*facts['points'][k][1-axis]) for items in stations]
        result.append(dict(points=representatives, stations=stations, side=side))
    # Opposite chains with identical coordinates carry the same definitions.
    preferred = -1 if axis == 0 else 1
    unique = []
    for run in sorted(result, key=lambda r: r['side'] != preferred):
        values = [facts['points'][k][axis] for k in run['points']]
        if not any(len(values) == len(old['points']) and all(abs(v-facts['points'][k][axis]) <= tolerance
                   for v, k in zip(values, old['points'])) for old in unique):
            unique.append(run)
    return unique


def local_pair(facts, left, right, axis, side):
    return min(((a, b) for a in left for b in right), key=lambda pair: (
        abs(facts['points'][pair[0]][1-axis]-facts['points'][pair[1]][1-axis]),
        -side*sum(facts['points'][k][1-axis] for k in pair), pair))


def direct_tangent_pair(facts, pair, straights, axis, tolerance):
    """Replace a composite span only when its remainder is an adjacent proven R."""
    points=facts['points'];low,high=sorted(points[k][axis] for k in pair)
    outer=set(facts['boundaryLoops'][0]['segments']);candidates=[]
    for straight in straights:
        ids=straight['points'];shared=set(ids)&set(pair)
        if len(shared)!=1 or not all(low-tolerance<=points[k][axis]<=high+tolerance for k in ids):continue
        tangent=next(k for k in ids if k not in shared)
        replaced=next(k for k in pair if k not in shared)
        for arc in facts['segments']:
            if arc['id'] not in outer or arc['kind']!='ARC' or not axis_aligned_arc(arc,tolerance):continue
            ends=[arc['startPoint'],arc['endPoint']]
            if tangent not in ends:continue
            other=ends[1-ends.index(tangent)]
            if abs(points[other][axis]-points[replaced][axis])<=tolerance:
                candidates.append(ids);break
    return candidates[0] if len(candidates)==1 else list(pair)


def hole_datums(facts, ids, axis, tolerance):
    """Prefer a real straight edge spanning the hole row, never a virtual corner."""
    other = [facts['points'][k][1-axis] for k in ids]
    mean = sum(facts['points'][k][axis] for k in ids)/len(ids)
    middle = sum(other)/len(other)
    side = 1 if middle > (facts['bounds'][1-axis]+facts['bounds'][3-axis])/2+tolerance else -1
    candidates = []
    outer = set(facts['boundaryLoops'][0]['segments'])
    for s in facts['segments']:
        if s['id'] not in outer or s['kind'] != 'LINE' or abs(s['start'][axis]-s['end'][axis]) > tolerance:
            continue
        low, high = sorted((s['start'][1-axis], s['end'][1-axis]))
        if low-tolerance <= min(other) and high+tolerance >= max(other):
            key = max((s['startPoint'], s['endPoint']), key=lambda k: (side*facts['points'][k][1-axis], k))
            candidates.append((abs(facts['points'][key][axis]-mean), facts['points'][key][axis], key))
    if not candidates:
        return [facts['datum']]
    nearest = min(c[0] for c in candidates)
    datum = min((c for c in candidates if c[0] <= nearest+tolerance), key=lambda c: (c[1], c[2]))[2]
    positions = [facts['points'][k][axis] for k in ids]
    # Close only a single row bounded by real edges on both sides. A staggered
    # pattern or an intervening boundary is not one continuous edge/pitch chain.
    if max(other)-min(other) <= tolerance and max(positions)-min(positions) > tolerance:
        low = [c for c in candidates if c[1] < min(positions)-tolerance]
        high = [c for c in candidates if c[1] > max(positions)+tolerance]
        interior = [c for c in candidates if min(positions)-tolerance <= c[1] <= max(positions)+tolerance]
        if low and high and not interior:
            return [max(low,key=lambda c:c[1])[2],min(high,key=lambda c:c[1])[2]]
    return [datum]


def tangent_straights(facts, axis, tolerance):
    """Direct straight lengths beside R transitions, including short machining flats."""
    by_id = {s['id']: s for s in facts['segments']}
    segments = [by_id[k] for k in facts['boundaryLoops'][0]['segments']]
    area = sum(s['start'][0]*s['end'][1]-s['end'][0]*s['start'][1] for s in segments)
    candidates = []
    for i,s in enumerate(segments):
        if (s['kind'] != 'LINE' or abs(s['end'][1-axis]-s['start'][1-axis]) > tolerance
                or abs(s['end'][axis]-s['start'][axis]) <= tolerance):
            continue
        adjacent = ((s['start'],segments[i-1]),(s['end'],segments[(i+1)%len(segments)]))
        if not any(a['kind']=='ARC' and axis_aligned_arc(a,tolerance)
                   and abs(p[axis]-a['center'][axis])<=tolerance for p,a in adjacent):
            continue
        direction = 1 if s['end'][axis]>s['start'][axis] else -1
        side = (-1 if axis==0 else 1)*direction*(1 if area>0 else -1)
        candidates.append(dict(points=sorted((s['startPoint'],s['endPoint']),key=lambda k:facts['points'][k][axis]),side=side))
    unique = []
    for run in sorted(candidates,key=lambda r:r['side']!=(-1 if axis==0 else 1)):
        if not any(all(abs(facts['points'][a][axis]-facts['points'][b][axis])<=tolerance
                       for a,b in zip(run['points'],old['points'])) for old in unique):
            unique.append(run)
    return unique
