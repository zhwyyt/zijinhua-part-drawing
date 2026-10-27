"""Common shape evidence -> geometric family -> specialized verification route.

Classification describes geometry, never a Tekla command or fabrication process.
"""
import math

from shapely.geometry import LineString, Polygon

from .analytic_geometry import sub, dot, norm, unit, frame_axes, project, line, check_hole_topology
from .solid_prisms import value, match_prisms


def _frame(normal, preferred):
    axes = frame_axes(preferred)
    sign = next(a for a in (axes[2], axes[1], axes[0]) if abs(dot(normal, a)) > 1e-8)
    if dot(normal, sign) < 0:
        normal = [-v for v in normal]
    direction = next(a for a in axes if abs(dot(a, normal)) < 1-1e-8)
    x = unit([a-dot(direction, normal)*n for a, n in zip(direction, normal)])
    y = [normal[1]*x[2]-normal[2]*x[1], normal[2]*x[0]-normal[0]*x[2], normal[0]*x[1]-normal[1]*x[0]]
    return dict(origin=preferred['origin'], axisX=x, axisY=y)


def _offset_strip(points, tolerance):
    """Recognize a finite sharp-fold strip by reconstructing both offset sides."""
    if len(points) < 6 or len(points) % 2:
        return None
    polygon = Polygon(points)
    if not polygon.is_valid:
        return None
    half = len(points)//2
    for i in range(len(points)):
        ring = points[i:]+points[:i]
        a, b = ring[:half], ring[half:][::-1]
        thickness = math.dist(a[0], b[0])
        if thickness <= tolerance or abs(math.dist(a[-1], b[-1])-thickness) > tolerance:
            continue
        middle = [[(x+y)/2 for x,y in zip(p,q)] for p,q in zip(a,b)]
        path = LineString(middle)
        if not path.is_simple or any(math.dist(p,q) <= thickness for p,q in zip(middle,middle[1:])):
            continue
        directions = [unit(sub(q,p)) for p,q in zip(middle,middle[1:])]
        angles = [math.degrees(math.acos(max(-1,min(1,dot(p,q))))) for p,q in zip(directions,directions[1:])]
        if not angles or max(angles) < 1e-6:
            continue
        reconstructed = path.buffer(thickness/2, cap_style=2, join_style=2)
        if (polygon.boundary.hausdorff_distance(reconstructed.boundary) <= tolerance and
                polygon.symmetric_difference(reconstructed).area <= tolerance*polygon.length):
            return dict(thickness=thickness, centerline=middle, turnsDegrees=angles,
                        basis='COMPLETE_SECTION_EQUALS_CONSTANT_NORMAL_OFFSET_STRIP')
    return None


def collect_shape_facts(part, preferred, path, tolerance):
    """Facts precede classification; holes and machining effects are not proved here."""
    facts = dict(schemaVersion='commonShapeFacts.v1', scope='BASE_GEOMETRY_BEFORE_MACHINING',
                 prisms=[], contourPlane=None, issues=[], sourceRefs=[])
    raw_node = part.get('rawSolid')
    facts['rawStatus'] = raw_node.get('status','ABSENT') if isinstance(raw_node,dict) else 'ABSENT'
    solid = None
    if facts['rawStatus'] == 'READ':
        solid = value(raw_node)
        faces, edges = value(solid['faces']), value(solid['edges'])
        facts['sourceRefs'].append(path+'/rawSolid')
        facts['faceCount'], facts['edgeCount'] = len(faces), len(edges)
        facts['straightEdges'] = bool(edges) and all(e['apiType']=='EDGE_TYPE_NORMAL' for e in edges)
        normals = []
        for face in faces:
            normal = unit(value(face['normal']))
            if any(abs(dot(normal,n)) > 1-1e-8 for n in normals):
                continue
            normals.append(normal)
            if not facts['straightEdges']:
                continue  # Curved edges require their independent analytic parameters.
            frame = _frame(normal, preferred)
            try:
                loops = value(face['loops'])
                if len(loops) != 1:
                    continue
                points = [project(p,frame)[:2] for p in value(loops[0]['vertices'])]
                outline = dict(kind='OUTLINE', segments=[line(a,b,[path+'/rawSolid'])
                               for a,b in zip(points,points[1:]+points[:1])])
                check_hole_topology(outline['segments'],[],tolerance)
                shell = match_prisms(solid,[outline],frame,tolerance,path+'/rawSolid/value',origin_id=part['id'])
                spans = [max(p[i] for p in points)-min(p[i] for p in points) for i in (0,1)]
                facts['prisms'].append(dict(frame=frame, section=points, spans=spans,
                    depth=shell['thickness'], shell=shell,
                    offsetStrip=_offset_strip(points,tolerance)))
            except (ValueError,KeyError,TypeError,IndexError) as error:
                facts['issues'].append(dict(code='AXIS_NOT_COMPLETE_PRISM',normal=normal,message=str(error)))
        facts['normalDirections'] = normals
    # Runtime type determines the meaning of this API field, never the result type.
    if part['runtimeType'] == 'Tekla.Structures.Model.ContourPlate':
        try:
            contour = value(value(part['modelingContour']))
            points = [project(c['point'],preferred) for c in contour]
            if len(points) >= 3:
                spread = max(p[2] for p in points)-min(p[2] for p in points)
                plane = dict(spread=spread,frame=preferred,sourceRef=path+'/modelingContour')
                if solid is not None:
                    vertices = [project(p,preferred) for f in value(solid['faces'])
                                for loop in value(f['loops']) for p in value(loop['vertices'])]
                    lo,hi = min(p[2] for p in vertices),max(p[2] for p in vertices)
                    plane['rawTwoLevels'] = hi-lo > tolerance and all(
                        min(abs(p[2]-lo),abs(p[2]-hi)) <= tolerance for p in vertices)
                facts['contourPlane'] = plane
                facts['sourceRefs'].append(path+'/modelingContour')
        except (ValueError,KeyError,TypeError,IndexError) as error:
            facts['issues'].append(dict(code='CONTOUR_PLANE_UNAVAILABLE',message=str(error)))
    return facts


def classify_shape(facts, tolerance):
    """Pure facts-only dispatch. A candidate is never permission to draw."""
    result = dict(schemaVersion='geometricPartType.v1',type='UNKNOWN',status='UNRESOLVED',
                  scope=facts['scope'],route=None,sourceRefs=facts['sourceRefs'],
                  basis='INSUFFICIENT_GEOMETRIC_EVIDENCE',missingProofs=[])
    flat = [p for p in facts['prisms'] if p['depth'] < min(p['spans'])-tolerance]
    if len(flat) == 1:
        return dict(result,type='FLAT_PLATE',status='CLASSIFIED',route='FLAT_PLATE',
                    basis='UNIQUE_THIN_COMPLETE_PRISM',frame=flat[0]['frame'],thickness=flat[0]['depth'])
    if len(flat) > 1:
        return dict(result,status='AMBIGUOUS',basis='MULTIPLE_THIN_DIRECTIONS',missingProofs=['UNIQUE_PLATE_NORMAL'])
    plane = facts['contourPlane']
    contour_eligible = facts['rawStatus']=='ABSENT' or (
        facts['rawStatus']=='READ' and not facts['straightEdges'] and plane and plane.get('rawTwoLevels'))
    if plane and plane['spread'] <= tolerance and contour_eligible:
        return dict(result,type='FLAT_PLATE',status='CANDIDATE',route='FLAT_PLATE',
                    basis='PLANAR_BOUNDARY_REQUIRES_SPECIALIZED_SHELL_PROOF',frame=plane['frame'],
                    missingProofs=['SPECIALIZED_FLAT_PLATE_GEOMETRY'])
    folded = [p for p in facts['prisms'] if p['offsetStrip'] and p['depth'] > p['offsetStrip']['thickness']+tolerance]
    if len(folded) == 1:
        return dict(result,type='BENT_PLATE',status='CLASSIFIED',basis='CONSTANT_THICKNESS_FOLDED_SECTION',
                    thickness=folded[0]['offsetStrip']['thickness'],foldGeometry=folded[0]['offsetStrip'],
                    missingProofs=['BENT_PLATE_FINAL_GEOMETRY_AND_EXPRESSION_ROUTE'])
    if len(folded) > 1:
        return dict(result,status='AMBIGUOUS',basis='MULTIPLE_FOLDED_SECTIONS',missingProofs=['UNIQUE_PLATE_SECTION'])
    if facts['prisms']:
        return dict(result,type='PRISMATIC_SECTION',status='CLASSIFIED',basis='COMPLETE_NONPLATE_SECTION',
                    missingProofs=['SECTION_SPECIFIC_GEOMETRY_AND_EXPRESSION_ROUTE'])
    return dict(result,missingProofs=['COMPLETE_BASE_SHAPE_OR_ANALYTIC_SURFACE_EVIDENCE'])
