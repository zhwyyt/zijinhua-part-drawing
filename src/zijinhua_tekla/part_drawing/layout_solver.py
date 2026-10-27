"""M5 finite candidate search. Expression commands are immutable inputs."""
import copy
from functools import lru_cache

from ezdxf.fonts import fonts
from shapely.geometry import LineString, Polygon, box

from .expression_coverage import display_round


MAX_VISITS = 4096
LANES = 8
MAX_NOTE_CANDIDATES = 48


class LayoutOverflow(ValueError):
    def __init__(self, report):
        self.report = report
        super().__init__('LAYOUT_OVERFLOW: '+report['reason']+'; object='+report['blockedObject'])


@lru_cache(maxsize=1)
def _font():
    face = fonts.find_font_face('arial.ttf')
    if face.family.lower() != 'arial':
        raise ValueError('LAYOUT_FONT_UNAVAILABLE: Arial')
    return fonts.make_font(face.filename, 1)


@lru_cache(maxsize=2048)
def text_width(text, height):
    return _font().text_width(text)*height


def note_bounds(text, at, height, leader=False):
    width = text_width(text, height)+.2*height
    # Native leader text has extra advance/bearings (SDK and DXF live evidence).
    # Reserve these before collision solving; saved glyphs still must fit.
    pad = .5*height if leader else 0
    half_height = (.8 if leader else .7)*height
    return [at[0]-width/2-pad, at[1]-half_height, at[0]+width/2+pad, at[1]+half_height]


def search_candidates(units, conflicts, fits, budget=MAX_VISITS):
    """Deterministic DFS; every attempted candidate consumes the shared budget."""
    report = dict(status='SEARCHING', budget=budget, visited=0, backtracks=0,
                  blockedObject='', conflicts=[], reason='NO_FEASIBLE_CANDIDATE')
    chosen = []

    def search(depth):
        if depth == len(units):
            return True
        key, generate = units[depth]
        report['blockedObject'] = key
        for candidate in generate(chosen):
            if report['visited'] >= budget:
                report['reason'] = 'SEARCH_BUDGET_EXHAUSTED'
                return False
            report['visited'] += 1
            found = conflicts(candidate, chosen)
            if not found and not fits(chosen+[candidate]):
                found = [dict(code='TEMPLATE_ENVELOPE_OVERFLOW', objects=[key])]
            if found:
                report['conflicts'] = (report['conflicts']+found)[-32:]
                continue
            chosen.append(candidate)
            if search(depth+1):
                return True
            chosen.pop()
            report['backtracks'] += 1
            if report['reason'] == 'SEARCH_BUDGET_EXHAUSTED':
                return False
        return False

    if not search(0):
        report['status'] = 'LAYOUT_OVERFLOW'
        raise LayoutOverflow(report)
    report.update(status='SOLVED', reason=None, blockedObject=None,
                  selections=[dict(key=c['key'], choice=c.get('choice', 0)) for c in chosen])
    return chosen, report


def _box_conflicts(a, b):
    errors = []
    for first in a['boxes']:
        area = box(*first['bounds'])
        for second in b['boxes']:
            if area.intersection(box(*second['bounds'])).area > 1e-8:
                errors.append(dict(code='TEXT_OVERLAP', objects=[first['key'], second['key']]))
        for line in b['lines']:
            # Native dimension backgrounds mask witness lines. Saved DXF must
            # independently prove the remaining visible portions (not just SDK).
            if line['role'] == 'witness' and first.get('nativeDimension'):
                continue
            if area.intersection(LineString(line['points'])).length > 1e-8:
                errors.append(dict(code='LINE_THROUGH_TEXT', objects=[first['key'], line['key']]))
    return errors


def scene_conflicts(candidate, chosen):
    errors = []
    for old in chosen:
        errors.extend(_box_conflicts(candidate, old))
        errors.extend(_box_conflicts(old, candidate))
        for first in candidate['lines']:
            for second in old['lines']:
                if 'leader' not in (first['role'], second['role']):
                    continue
                if LineString(first['points']).crosses(LineString(second['points'])):
                    errors.append(dict(code='LEADER_CROSSING', objects=[first['key'], second['key']]))
        for a in candidate['spans']:
            for b in old['spans']:
                if a['axis'] != b['axis'] or a['side'] != b['side']:
                    continue
                overlap = min(a['high'], b['high'])-max(a['low'], b['low']) > 1e-6
                if overlap and a['side']*(a['line']-b['line']) < a['clearance']-1e-6:
                    errors.append(dict(code='DIMENSION_LAYER_ORDER', objects=[a['key'], b['key']]))
                if a['overall'] and a['side']*(a['line']-b['line']) < a['clearance']-1e-6:
                    errors.append(dict(code='OVERALL_NOT_OUTERMOST', objects=[a['key'], b['key']]))
    return errors


def _witness_stays_outside(point, side, cross, line, interior):
    start, end = list(point), list(point)
    start[cross] = point[cross]+side*0.2
    end[cross] = line
    if side*(end[cross]-start[cross]) <= 0:
        return False
    return not interior.intersects(LineString([start, end]))


def witness_curves(dimensions, bounds, unit, polygon=None):
    result = []
    interior = None if polygon is None else polygon.buffer(-0.05)
    for dim in dimensions:
        axis = 'xy'.index(dim['axis']); cross = 1-axis; side = dim['up'][cross]
        line = dim['coordinates'][0][cross]+side*dim['distance']
        for index, point in enumerate(dim['coordinates']):
            if any(a < index < b for a, b in dim['combineRuns']):
                continue
            base = point[cross]
            # A contour anchor keeps its own extension when that ray stays
            # outside the plate. Otherwise the station uses the envelope edge,
            # so a lower-edge witness is not drawn on the upper silhouette.
            exposed = (dim['key'].startswith('boundary') and not dim.get('local')
                       and interior is not None and not interior.is_empty
                       and _witness_stays_outside(point, side, cross, line, interior))
            if not dim.get('local') and not exposed and (
                    dim['role'] == 'overall' or dim['key'].startswith('boundary')
                    or not dim['points'][index].startswith('hole:')):
                base = bounds[cross+(2 if side > 0 else 0)]
            start, end = list(point), list(point)
            start[cross] = base+side*3*unit
            if dim.get('local'):
                start[cross] = (max if side > 0 else min)(start[cross], line-side*4*unit)
            end[cross] = line+side*2*unit
            if side*(end[cross]-start[cross]) <= 0:
                raise ValueError('Insufficient clearance for a dimension witness')
            result.append(dict(key=f"witness:{dim['key']}:{index}", kind='line', role='witness',
                               feature=dim['key'], start=start, end=end))
    return result


def dimension_boxes(dim, height, decimals):
    axis = 'xy'.index(dim['axis']); cross = 1-axis
    line = dim['coordinates'][0][cross]+dim['up'][cross]*dim['distance']
    runs = {a: b for a, b in dim['combineRuns']}; result = []; index = 0
    while index < len(dim['values']):
        end = runs.get(index, index+1)
        value = f"{display_round(dim['values'][index], decimals):.{decimals}f}"
        text = f'{end-index}@{value}' if end-index > 1 else value
        low, high = dim['coordinates'][index][axis], dim['coordinates'][end][axis]
        width = text_width(text, height)*.85
        # Tekla's short text is automatically placed at either end. Bound all
        # possible positions instead of pretending to know its final glyph box.
        short = high-low < width+.8*height
        extent = [(low+high-width)/2-.15*height, (low+high+width)/2+.15*height]
        if short:
            extent = ([low-width-height, low] if index == 0 else
                      [high, high+width+height] if end == len(dim['values']) else
                      [low-width-height, high+width+height])
        bounds = ([extent[0], line+.2*height, extent[1], line+1.65*height] if axis == 0 else
                  [line-1.65*height, extent[0], line-.2*height, extent[1]])
        result.append(dict(key=f"{dim['key']}:text:{index}", text=text, bounds=bounds,
                           nativeDimension=True, placement='SHORT_NATIVE_ENVELOPE' if short else 'FONT_METRICS'))
        index = end
    return result


def dimension_scene(dimensions, bounds, unit, decimals, polygon=None):
    height = 2.5*unit
    scene = dict(key=dimensions[0].get('columnGroup', dimensions[0]['key']), dimensions=dimensions,
                 notes=[], radii=[], boxes=[], lines=[], spans=[])
    for dim in dimensions:
        axis = 'xy'.index(dim['axis']); cross = 1-axis; side = dim['up'][cross]
        line = dim['coordinates'][0][cross]+side*dim['distance']
        ends = [list(dim['coordinates'][i]) for i in (0, -1)]
        for p in ends:
            p[cross] = line
        scene['lines'].append(dict(key=dim['key'], role='dimension', points=ends))
        scene['boxes'].extend(dimension_boxes(dim, height, decimals))
        scene['spans'].append(dict(key=dim['key'], axis=axis, side=side, low=ends[0][axis],
                                   high=ends[1][axis], line=line, clearance=8*unit,
                                   overall=dim['role'] == 'overall'))
    for curve in witness_curves(dimensions, bounds, unit, polygon):
        scene['lines'].append(dict(key=curve['key'], role='witness', points=[curve['start'], curve['end']]))
    return scene


def dimension_candidates(members, groups, bounds, unit, decimals, chosen, polygon=None):
    first = members[0]; axis = 'xy'.index(first['axis']); cross = 1-axis
    preferred = first['up'][cross]
    allowed = set.intersection(*(set(groups[d['key']].get('allowedSides', [d['up'][cross]])) for d in members))
    sides = [preferred]+[s for s in sorted(allowed) if s != preferred]
    for side in sides:
        if side not in allowed:
            continue
        edge = bounds[cross+(2 if side > 0 else 0)]
        seed = (max if side > 0 else min)(d['coordinates'][0][cross]+side*d['distance'] for d in members)
        if side != preferred:
            seed = edge+side*max(groups[d['key']]['lane'] for d in members)*unit
        if first['role'] == 'overall':
            same = [s['line'] for c in chosen for s in c['spans'] if s['axis'] == axis and s['side'] == side]
            if same:
                seed = (max if side > 0 else min)(seed, (max if side > 0 else min)(same)+side*12*unit)
        else:
            overlaps = [s['line'] for c in chosen for s in c['spans'] if s['axis']==axis and s['side']==side and
                        any(min(d['coordinates'][-1][axis],s['high'])-max(d['coordinates'][0][axis],s['low'])>1e-6
                            for d in members)]
            if overlaps:
                seed = (max if side > 0 else min)(seed,(max if side > 0 else min)(overlaps)+side*10*unit)
        for lane in range(LANES):
            commands = copy.deepcopy(members)
            for d in commands:
                d['up'] = [0, side] if axis == 0 else [side, 0]
                d['distance'] = side*(seed+side*lane*10*unit-d['coordinates'][0][cross])
            scene = dimension_scene(commands, bounds, unit, decimals, polygon)
            scene['choice'] = dict(side=side, lane=lane)
            yield scene


def scene_envelope(chosen, bounds, padding):
    points = [bounds[:2], bounds[2:]]
    for scene in chosen:
        for rect in scene['boxes']:
            points.extend([rect['bounds'][:2], rect['bounds'][2:]])
        points.extend(p for line in scene['lines'] for p in line['points'])
    return [min(p[0] for p in points)-padding, min(p[1] for p in points)-padding,
            max(p[0] for p in points)+padding, max(p[1] for p in points)+padding]


def note_candidates(note, polygon, view_scale):
    height = note['height']*view_scale
    placements = note.get('candidates', [dict(at=note['at'], tip=note['tip'])])
    for index, placement in enumerate(placements[:MAX_NOTE_CANDIDATES]):
        command = {k: copy.deepcopy(v) for k, v in note.items() if k != 'candidates'}
        command.update(placement)
        bounds = note_bounds(note['text'], command['at'], height, command['tip'] is not None)
        area = box(*bounds)
        if note['kind'] == 'label' and not polygon.contains(area):
            continue
        if polygon.boundary.intersects(area):
            continue
        lines = []
        if command['tip'] is not None:
            leader = LineString([command['tip'], command['at']])
            if any(leader.intersection(Polygon(ring).buffer(-1e-6)).length > 1e-6 for ring in polygon.interiors):
                continue
            lines.append(dict(key=note['key'], role='leader', points=[command['tip'], command['at']]))
        yield dict(key=note['key'], choice=index, dimensions=[], notes=[command], radii=[],
                   boxes=[dict(key=note['key'], text=note['text'], bounds=bounds)], lines=lines, spans=[])


def radius_candidates(radius, unit):
    # The native radius text direction is owned by Tekla. Reserve the measured
    # arc anchor neighbourhood rather than using an invented text insertion point.
    for lane in range(LANES):
        command = dict(radius, distance=radius['distance']+lane*5*unit)
        tip = radius['points'][1]; reach = command['distance']+5*unit
        bounds = [tip[0]-reach, tip[1]-reach, tip[0]+reach, tip[1]+reach]
        yield dict(key=radius['key'], choice=lane, dimensions=[], notes=[], radii=[command],
                   boxes=[dict(key=radius['key'], text='R', bounds=bounds, nativeDimension=True,
                               placement='NATIVE_RADIUS_ENVELOPE')], lines=[], spans=[])


def solve_native_layout(dimensions, notes, radii, scheme, polygon, context):
    bounds, unit = context['bounds'], context['unit']
    groups = {g['key']: g for g in scheme['groups']}
    units = []; assigned = set()
    # Native radius placement has less freedom than linear dimensions. Reserve
    # it first so a nearby dimension can move without exhausting outer lanes.
    for radius in radii:
        units.append((radius['key'], lambda _, radius=radius: radius_candidates(radius, unit)))
    ordered = sorted(dimensions, key=lambda d: (d['role'] == 'overall',
                     0 if d['key'].startswith(('holes', 'straight')) else 1, sum(d['values'])))
    for dim in ordered:
        if dim['key'] in assigned:
            continue
        members = [d for d in dimensions if d.get('columnGroup') == dim['columnGroup']] if dim.get('columnGroup') else [dim]
        assigned.update(d['key'] for d in members)
        units.append((dim.get('columnGroup', dim['key']), lambda chosen, members=members, polygon=polygon:
                      dimension_candidates(members, groups, bounds, unit, context['decimals'], chosen, polygon)))
    for note in notes:
        candidates = list(note_candidates(note, polygon, context['viewScale']))
        if not candidates:
            raise LayoutOverflow(dict(status='LAYOUT_OVERFLOW',reason='NO_GEOMETRIC_NOTE_CANDIDATE',
                blockedObject=note['key'],visited=0,budget=context.get('budget',MAX_VISITS),backtracks=0,
                conflicts=[dict(code='NOTE_OUTSIDE_MATERIAL_OR_CROSSES_HOLE',objects=[note['key']])]))
        units.append((note['key'], lambda _, candidates=candidates: candidates))

    def conflicts(candidate, chosen):
        # Internal chain labels must also fit. Disjoint members of one column
        # share an intentional line but may not share text occupancy.
        internal = []
        for i, a in enumerate(candidate['boxes']):
            for b in candidate['boxes'][i+1:]:
                if box(*a['bounds']).intersection(box(*b['bounds'])).area > 1e-8:
                    internal.append(dict(code='CHAIN_TEXT_OVERLAP', objects=[a['key'], b['key']]))
        for rect in candidate['boxes']:
            if rect.get('placement') != 'NATIVE_RADIUS_ENVELOPE' and polygon.boundary.intersects(box(*rect['bounds'])):
                internal.append(dict(code='TEXT_CROSSES_MATERIAL_BOUNDARY',objects=[rect['key']]))
        static = dict(boxes=[],lines=context.get('staticLines',[]))
        return internal+_box_conflicts(candidate,static)+scene_conflicts(candidate, chosen)

    chosen, report = search_candidates(units, conflicts, context['fits'], context.get('budget', MAX_VISITS))
    by_key = {d['key']: d for c in chosen for d in c['dimensions']}
    placed_notes = {n['key']: n for c in chosen for n in c['notes']}
    placed_radii = {r['key']: r for c in chosen for r in c['radii']}
    report.update(textModel='ARIAL_FONT_METRICS_WITH_NATIVE_PLACEMENT_ENVELOPES',
                  textBoxes=[b for c in chosen for b in c['boxes']],
                  envelope=scene_envelope(chosen, bounds, context['padding']))
    return ([by_key[d['key']] for d in dimensions], [placed_notes[n['key']] for n in notes],
            [placed_radii[r['key']] for r in radii], report)
