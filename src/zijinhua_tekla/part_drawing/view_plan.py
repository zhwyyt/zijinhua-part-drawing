"""P4: choose a free paper rectangle from an explicit template snapshot."""
import math

from .template_snapshot import validate_snapshot, overlaps, contains


def build_view_plan(snapshot, envelope, scale=None, allow_overflow=False):
    """Place a model envelope. scale is the view denominator; paper size is model size / scale.

    allow_overflow is a preview placement: if no free rectangle holds the view,
    center it on the sheet and mark previewOverflow. The sheet size stays unchanged.
    """
    validate_snapshot(snapshot)
    view_scale = snapshot['scale'] if scale is None else scale
    if isinstance(view_scale, bool) or not isinstance(view_scale, (int, float)) or not math.isfinite(view_scale) or view_scale <= 0:
        raise ValueError('VIEW_SCALE_INVALID')
    safe = snapshot['safePage']
    obstacles = [r['bounds'] for r in snapshot['occupiedRegions']]
    width, height = (envelope[2]-envelope[0])/view_scale, (envelope[3]-envelope[1])/view_scale
    if not all(math.isfinite(v) for v in envelope) or width <= 0 or height <= 0:
        raise ValueError('VIEW_ENVELOPE_INVALID')
    xs = sorted(set([safe[0], safe[2]]+[b[i] for b in obstacles for i in (0, 2)]))
    candidates = []
    for i, left in enumerate(xs):
        for right in xs[i+1:]:
            if right-left < width:
                continue
            spans = sorted((b[1], b[3]) for b in obstacles if b[0] < right-1e-6 and b[2] > left+1e-6)
            bottom = safe[1]
            for low, high in spans+[(safe[3], safe[3])]:
                if low-bottom >= height:
                    candidates.append([left, bottom, right, low])
                bottom = max(bottom, high)
    preview = False
    if not candidates:
        if not allow_overflow:
            raise ValueError('LAYOUT_OVERFLOW: view does not fit the live template')
        sheet = snapshot['sheetSize']
        available = [sheet[0]/2-width/2, sheet[1]/2-height/2, sheet[0]/2+width/2, sheet[1]/2+height/2]
        preview = True
    else:
        available = min(candidates, key=lambda b: (-(b[2]-b[0])*(b[3]-b[1]), b))
        assert contains(safe, available) and not any(overlaps(available, b) for b in obstacles)
    plan = dict(schemaVersion='nativeViewPlan.v1', availableArea=available, plannedEnvelope=envelope,
                templateSignature=snapshot['source']['signature'], snapshotDigest=snapshot['snapshotDigest'],
                templatePartId=snapshot['source']['partId'], templateSheet=snapshot['source']['sheetNumber'],
                fontHeight=snapshot['style']['fontHeight'], scale=view_scale, sheetSize=snapshot['sheetSize'])
    if preview:
        plan['previewOverflow'] = True
    return plan


def audit_view_plan(plan, raw):
    """Check fresh SDK evidence, including the post-save template identity."""
    view = plan['viewPlan']; errors = []
    if raw.get('templateSignature') != view['templateSignature']:
        errors.append(dict(code='TEMPLATE_CHANGED_AFTER_SAVE'))
    if raw['sheetSize'] != view['sheetSize'] or raw['scale'] != view['scale']:
        errors.append(dict(code='VIEW_TEMPLATE_SIZE_OR_SCALE_CHANGED'))
    values = raw['origin'][:2]+raw['frameOrigin'][:2]+[raw['width'], raw['height']]
    if not all(math.isfinite(v) for v in values) or raw['width'] <= 0 or raw['height'] <= 0:
        errors.append(dict(code='VIEW_BOUNDS_INVALID'))
        return errors
    low = [raw['origin'][i]+raw['frameOrigin'][i] for i in (0, 1)]
    bounds = low+[low[0]+raw['width'], low[1]+raw['height']]
    if not contains(view['availableArea'], bounds, .1):
        errors.append(dict(code='VIEW_OUTSIDE_TEMPLATE_FREE_AREA', bounds=bounds))
    return errors
