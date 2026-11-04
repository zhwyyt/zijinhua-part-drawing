"""Conservative empty rectangles from actual sheet-level template presentation."""
import itertools

from shapely.geometry import box

from .contracts import digest, require, seal
from .foundation_studies import _nodes


def template_regions(capture, margin=50):
    page=capture['page'];size=page['drawing']['Layout']['SheetSize']
    width,height=size['Width'],size['Height'];obstacles=[]
    def extent(node, frame=False):
        points=[]
        for line in _nodes(node,'.LinePrimitive'):
            a,b=line['StartPoint'],line['EndPoint']
            # The sheet border is an enclosure, not a filled obstacle.
            if frame and (abs(a['X']-b['X'])>=width*.9 or abs(a['Y']-b['Y'])>=height*.9):continue
            points.extend([(a['X'],a['Y']),(b['X'],b['Y'])])
        for text in _nodes(node,'.TextPrimitive'):
            x,y=text['Position']['X'],text['Position']['Y'];h=text['Height']
            advance=sum(1 if ord(c)>255 else .7 for c in text['Text'])
            points.extend([(x-h,y-h),(x+h*max(1,advance),y+h*1.5)])
        if not points:return None
        return [min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)]
    for view in page['presentation']:
        if view['Position']['X']!=0 or view['Position']['Y']!=0:continue
        for segment in view['Primitives']:
            # Tables form one occupied area, including blank cells. Frame/title
            # segments span the sheet and are handled by their child groups.
            lines=list(_nodes(segment,'.LinePrimitive'))
            frame=(any(abs(l['StartPoint']['X']-l['EndPoint']['X'])>=width*.9 for l in lines) and
                   any(abs(l['StartPoint']['Y']-l['EndPoint']['Y'])>=height*.9 for l in lines))
            for node in segment['Primitives'] if frame else [segment]:
                bounds=extent(node,frame)
                if bounds is not None:obstacles.append(bounds)
    require(bool(obstacles),'TEMPLATE_OBSTACLES','No observed template occupancy')
    usable=[margin,margin,width-margin,height-margin]
    xs={usable[0],usable[2]};ys={usable[1],usable[3]}
    for a,b,c,d in obstacles:
        xs.update(x for x in (a-margin,c+margin) if usable[0]<x<usable[2])
        ys.update(y for y in (b-margin,d+margin) if usable[1]<y<usable[3])
    regions=[];shapes=[box(*b).buffer(margin) for b in obstacles]
    for a,c in itertools.combinations(sorted(xs),2):
        for b,d in itertools.combinations(sorted(ys),2):
            rect=box(a,b,c,d)
            if c-a<margin*2 or d-b<margin*2 or any(rect.intersection(s).area>1e-6 for s in shapes):continue
            regions.append([a,b,c,d])
    regions=sorted(regions,key=lambda r:(-(r[2]-r[0])*(r[3]-r[1]),r))
    maximal=[]
    for r in regions:
        if not any(box(*q).covers(box(*r)) for q in maximal):maximal.append(r)
    return seal(dict(schemaVersion='TemplateRegions.v1',captureDigest=digest(capture),sheet=[0,0,width,height],
                     margin=margin,obstacles=obstacles,regions=maximal,
                     overflow=[b for b in obstacles if not box(0,0,width,height).covers(box(*b))]))


def verify_template_clearance(plan, capture):
    observed=template_regions(capture)
    require(len(capture['page']['views'])==1,'TEMPLATE_VIEW','One main view required')
    view=capture['page']['views'][0]['view']
    x=view['Origin']['X']+view['FrameOrigin']['X'];y=view['Origin']['Y']+view['FrameOrigin']['Y']
    bounds=[x,y,x+view['Width'],y+view['Height']];frame=box(*bounds)
    conflicts=[b for b in observed['obstacles'] if frame.distance(box(*b))<observed['margin']-1e-6]
    fits=box(*plan['region']).buffer(1e-6).covers(frame)
    return seal(dict(schemaVersion='TemplateClearance.v1',captureDigest=digest(capture),planDigest=plan['digest'],
                     status='TEMPLATE_CLEAR' if not conflicts and not observed['overflow'] and fits else 'TEMPLATE_BLOCKED',
                     viewBounds=bounds,regionMatched=fits,conflicts=conflicts,overflow=observed['overflow']))
