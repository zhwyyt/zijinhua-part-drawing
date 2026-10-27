"""Read the actual Tekla DXF; retain exported geometry as the visual witness."""
import math
import re
from pathlib import Path

import ezdxf
from ezdxf import bbox
from ezdxf.addons import text2path
from ezdxf.path import bbox as path_bbox
from shapely.geometry import LineString, Point
from shapely.ops import unary_union


def sample_arc(center, radius, start, sweep, tolerance=.01):
    step=2*math.acos(max(-1,min(1,1-tolerance/max(radius,tolerance))))
    count=max(2,math.ceil(abs(sweep)/max(step,1e-6)))
    return [[center[0]+radius*math.cos(start+sweep*i/count),center[1]+radius*math.sin(start+sweep*i/count)] for i in range(count+1)]


def segment_path(segment):
    if segment['kind']=='LINE': return [segment['start'],segment['end']]
    c=segment['center']; a=math.atan2(segment['start'][1]-c[1],segment['start'][0]-c[0])
    b=math.atan2(segment['end'][1]-c[1],segment['end'][0]-c[0]); sign=-1 if segment['clockwise'] else 1
    return sample_arc(c,segment['radius'],a,sign*((sign*(b-a))%math.tau))


def boundary_matches(segment,paths,tolerance):
    if not paths: return False
    expected=LineString(segment_path(segment))
    actual=unary_union([LineString(p) for p in paths if len(p)>1])
    return not actual.is_empty and expected.hausdorff_distance(actual)<=tolerance


def load_render(path,readback):
    drawing=ezdxf.readfile(path)
    scale=readback['scale']; origin=readback['origin']
    local=lambda p:[(p[0]-origin[0])*scale,(p[1]-origin[1])*scale]
    paths=[];circles=[];texts=[];segments=[]
    for e in drawing.modelspace():
        kind=e.dxftype();identity=e.dxf.handle
        if kind=='LINE':
            p=[local(e.dxf.start),local(e.dxf.end)]
            if math.dist(*p)>1e-8: paths.append(dict(id=identity,points=p,kind=kind));segments.append(p)
        elif kind in ('ARC','CIRCLE'):
            center=local(e.dxf.center);r=e.dxf.radius*scale
            if kind=='CIRCLE':
                start,sweep=0,math.tau;circles.append(dict(id=identity,center=center,radius=r))
            else: start=math.radians(e.dxf.start_angle);sweep=math.radians((e.dxf.end_angle-e.dxf.start_angle)%360)
            paths.append(dict(id=identity,points=sample_arc(center,r,start,sweep),kind=kind,center=center,radius=r,start=start,sweep=sweep))
        elif kind in ('LWPOLYLINE','POLYLINE'):
            for sub in e.virtual_entities():
                if sub.dxftype()=='LINE':
                    p=[local(sub.dxf.start),local(sub.dxf.end)]
                elif sub.dxftype()=='ARC':
                    p=sample_arc(local(sub.dxf.center),sub.dxf.radius*scale,math.radians(sub.dxf.start_angle),math.radians((sub.dxf.end_angle-sub.dxf.start_angle)%360))
                else: continue
                paths.append(dict(id=identity,points=p,kind=kind))
        elif kind in ('TEXT','MTEXT'):
            text=e.dxf.text if kind=='TEXT' else e.plain_text()
            text=re.sub(r'\\U\+([0-9A-Fa-f]{4})',lambda m:chr(int(m[1],16)),text)
            if kind=='TEXT':e.dxf.text=text
            b=path_bbox(text2path.make_paths_from_entity(e),fast=False) if kind=='TEXT' and text.strip() else bbox.extents([e])
            if b.has_data:
                measured=dict(id=identity,text=text,min=local(b.extmin),max=local(b.extmax),height=e.dxf.height if kind=='TEXT' else e.dxf.char_height)
                # Text backgrounds include font bearings/advance, beyond ink.
                # Keep ink bounds for collisions and layout bounds for clipping.
                layout_box=bbox.extents([e])
                if layout_box.has_data:
                    measured.update(layoutMin=local(layout_box.extmin),layoutMax=local(layout_box.extmax))
                texts.append(measured)
    return dict(paths=paths,circles=circles,texts=texts,lines=segments,source=str(Path(path).resolve()))


def boundary_paths(segment,render,tolerance):
    expected=LineString(segment_path(segment)); paths=[]; refs=[]
    for p in render['paths']:
        points=p['points']
        # Native full circles can witness either analytic half without inventing coordinates.
        runs=[];run=[]
        for point in points:
            if expected.distance(Point(point))<=tolerance+.015: run.append(point)
            else:
                if len(run)>1:runs.append(run)
                run=[]
        if len(run)>1:runs.append(run)
        for run in runs:
            if len(run)==len(points) or p['kind'] in ('ARC','CIRCLE'):
                paths.append(run);refs.append(p['id'])
    return paths,refs


def render_png(dxf_path,png_path):
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.addons.drawing.config import Configuration, BackgroundPolicy, ColorPolicy
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    d=ezdxf.readfile(dxf_path)
    for e in d.modelspace().query('TEXT'):
        e.dxf.text=re.sub(r'\\U\+([0-9A-Fa-f]{4})',lambda m:chr(int(m[1],16)),e.dxf.text)
    fig=plt.figure(figsize=(16,11));ax=fig.add_axes([0,0,1,1])
    config=Configuration(background_policy=BackgroundPolicy.WHITE,color_policy=ColorPolicy.BLACK)
    Frontend(RenderContext(d),MatplotlibBackend(ax),config=config).draw_layout(d.modelspace(),finalize=True)
    fig.set_size_inches(16,11)
    fig.savefig(png_path,dpi=180);plt.close(fig)
