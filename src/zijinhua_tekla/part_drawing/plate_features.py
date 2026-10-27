"""P1 plate feature model: engineering features from facts, no paper units."""
import math

from .native_dimensions import contour_chains, hole_datums, tangent_straights


VERSION = 'plate-features.m1'


def outer_circle(facts,tolerance):
    identities=facts['boundaryLoops'][0]['segments']
    segments=[s for s in facts['segments'] if s['id'] in identities]
    if not segments or any(s['kind']!='ARC' for s in segments):return None
    first=segments[0];sweep=0
    for s in segments:
        if math.dist(s['center'],first['center'])>tolerance or abs(s['radius']-first['radius'])>tolerance:return None
        a,b=[math.atan2(s[k][1]-s['center'][1],s[k][0]-s['center'][0]) for k in ('start','end')]
        sweep+=(((-1 if s['clockwise'] else 1)*(b-a))%math.tau)
    if abs(sweep-math.tau)>1e-6:return None
    return dict(center=first['center'],radius=first['radius'],segments=segments)


def build_feature_model(facts, tolerance):
    """Per-axis contour runs, tangent flats and hole datums; computed only where planning uses them."""
    circular = outer_circle(facts, tolerance)
    hole_ids = [h['pointId'] for h in facts['holes']]
    axes = []
    for axis in (0, 1):
        item = dict(contourChains=None, tangentStraights=None, holeDatums=None)
        if not circular:
            item['tangentStraights'] = tangent_straights(facts, axis, tolerance)
            item['contourChains'] = contour_chains(facts, axis, tolerance)
        centred = circular and all(abs(facts['points'][k][axis]-circular['center'][axis]) <= tolerance for k in hole_ids)
        if hole_ids and not centred:
            item['holeDatums'] = hole_datums(facts, hole_ids, axis, tolerance)
        axes.append(item)
    return dict(schemaVersion='plateFeatureModel.v1', version=VERSION, circular=circular,
                holeIds=hole_ids, axes=axes)
