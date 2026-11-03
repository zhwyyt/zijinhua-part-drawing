import copy
import unittest
from pathlib import Path

from shapely.geometry import LineString, box, shape

from zijinhua_tekla.part_drawing.independent_layout.contracts import ContractError, digest, read_json, seal
from zijinhua_tekla.part_drawing.independent_layout.joint_candidates import (
    bind_part_number, bind_dimension_points, dimension_geometry, generate_joint_candidates)
from zijinhua_tekla.part_drawing.independent_layout.page_obstacles import _accepted_paths, geometry_json


def fixture():
    return read_json(Path(__file__).parent/'fixtures/part_drawing/independent-obstacles/joint-measured.json')


def reseal(value):
    return seal({k:v for k,v in value.items() if k!='digest'})


def bind_number(f):
    return bind_part_number(*(f[k] for k in ('scene','primitives','capture','ink','source')))


def prepared(f):
    s,_=bind_dimension_points(*(f[k] for k in ('scene','primitives','capture','ink')))
    return {**s,'associationTolerance':f['primitives']['comparisonToleranceNative']}


def options():
    return dict(clearance=2.5,dimensions={'edge:0':[0,250],'edge:5':[-250,0,250]},
                numberOffsets=[[0,0],[0,1000]],leaders=dict(keys=['leader:0','leader:1'],offsets=[[0,0]],channels=[]))


class JointCandidateTests(unittest.TestCase):
    def test_native_number_binds_both_masks_and_overflowing_real_glyphs(self):
        f=fixture();bound=bind_number(f)
        self.assertEqual('T2-10B-584',bound['ink']['text'])
        self.assertEqual(2,len([i for i in bound['obstacleIds'] if i.startswith('path:')]))
        self.assertGreater(bound['ink']['occupancyBox'][2],bound['ink']['maskBox'][2]+38)
        self.assertEqual(10,len(bound['ink']['glyphIndices']))

    def test_number_cannot_remove_foreign_owned_glyph_or_missing_mask(self):
        for mode in ('owner','mask'):
            f=fixture();ids=bind_number(f)['obstacleIds'];items=f['scene']['obstacles']
            if mode=='owner':next(o for o in items if o['id']==ids[0])['owner']='edge:0'
            else:items.remove(next(o for o in items if o['id'] in ids and o['kind']=='MASK'))
            f['scene']=reseal(f['scene'])
            with self.subTest(mode=mode),self.assertRaises(ContractError):bind_number(f)

    def test_number_rejects_wrong_part_and_ambiguous_mark(self):
        for mode in ('part','mark','text','mask'):
            f=fixture();objects=f['capture']['page']['views'][0]['objects']
            mark=next(o for o in objects if o['data']['type'].endswith('.Mark'))
            if mode=='part':next(o for o in objects if o['data']['type'].endswith('.Part'))['data']['ModelIdentifier']['ID']+=1
            elif mode=='mark':objects.append(copy.deepcopy(mark))
            elif mode=='text':mark['data']['Attributes']['Content'][0]['Value']='wrong'
            else:
                segment=next(s for v in f['capture']['page']['presentation'] for s in v['Primitives'] if s['Id']==1302)
                segment['Primitives'][1]['OuterLoop']['Segments'][0]['StartPoint']['X']+=1
            f['scene']['captureDigest']=f['ink']['captureDigest']=digest(f['capture'])
            f['ink']=reseal(f['ink']);f['scene']['inkDigest']=f['ink']['digest'];f['scene']=reseal(f['scene'])
            with self.subTest(mode=mode),self.assertRaises(ContractError):bind_number(f)

    def test_dimension_point_paths_are_bound_and_ambiguous_points_rejected(self):
        f=fixture();s,b=bind_dimension_points(*(f[k] for k in ('scene','primitives','capture','ink')))
        self.assertEqual(4,len(b))
        self.assertTrue(all(next(o for o in s['obstacles'] if o['id']==v['path'])['owner']==v['key'] for v in b))
        path=copy.deepcopy(next(p for p in f['ink']['paths'] if p['index']==int(b[0]['path'].split(':')[1])))
        path['index']=999;f['ink']['paths'].append(path)
        with self.assertRaisesRegex(ContractError,'DIMENSION_POINTS'):
            bind_dimension_points(*(f[k] for k in ('scene','primitives','capture','ink')))

    def test_dimension_moves_baseline_but_not_near_extension_ends_or_anchor_span(self):
        f=fixture();scene=prepared(f);sample=f['primitives']['samples'][0]
        initial=dimension_geometry(scene,sample,0,set());moved=dimension_geometry(scene,sample,250,set())
        self.assertEqual(initial['anchors'],moved['anchors'])
        self.assertAlmostEqual(initial['baselineDistance']+250,moved['baselineDistance'])
        for i in (0,1):
            old=initial['extensions'][i];new=moved['extensions'][i]
            self.assertEqual(old['near'],new['near'])
            for axis in (0,1):self.assertAlmostEqual(old['far'][axis]+moved['translation'][axis],new['far'][axis])
        self.assertFalse(moved['conflicts'])

    def test_dimension_preserves_same_side_and_rejects_missing_extension(self):
        f=fixture();s=prepared(f);sample=f['primitives']['samples'][0]
        with self.assertRaisesRegex(ContractError,'DIMENSION_SIDE'):dimension_geometry(s,sample,-10000,set())
        initial=dimension_geometry(s,sample,0,set())
        s['obstacles']=[o for o in s['obstacles'] if o['id']!=initial['extensions'][0]['path']]
        with self.assertRaisesRegex(ContractError,'DIMENSION_EXTENSION'):dimension_geometry(s,sample,100,set())

    def test_extension_binding_does_not_assume_one_unit_anchor_gap(self):
        f=fixture();s=prepared(f);sample=f['primitives']['samples'][0]
        original=dimension_geometry(s,sample,0,set())
        extension=original['extensions'][0];item=next(o for o in s['obstacles'] if o['id']==extension['path'])
        near,far=sorted(item['endpoints'],key=lambda p:abs(p[1]-extension['anchor'][1]))
        near[1]-=4
        moved=dimension_geometry(s,sample,250,set())
        self.assertEqual(near,moved['extensions'][0]['near'])

    def test_bound_points_move_and_own_mask_cannot_hide_an_extension(self):
        f=fixture();s=prepared(f);sample=f['primitives']['samples'][0]
        original=dimension_geometry(s,sample,0,set());moved=dimension_geometry(s,sample,250,set())
        points=[o for o in s['obstacles'] if o['owner']==sample['key'] and o['kind']=='STROKE' and not o.get('endpoints')]
        for point in points:
            old=shape(point['geometry']).centroid
            new=next(p['geometry'].centroid for p in moved['parts'] if p['id']==point['id'])
            self.assertAlmostEqual(old.x+moved['translation'][0],new.x,places=5)
            self.assertAlmostEqual(old.y+moved['translation'][1],new.y,places=5)
        extension=original['extensions'][0];middle=LineString([extension['near'],extension['far']]).interpolate(.5,normalized=True)
        mask=next(o for o in s['obstacles'] if o['owner']==sample['key'] and o['kind']=='MASK')
        mask['geometry']=geometry_json(box(middle.x-5,middle.y-5,middle.x+5,middle.y+5))
        self.assertTrue(any(c['code']=='OWN_EXTENSION_MASK' for c in dimension_geometry(s,sample,0,set())['conflicts']))

    def test_exact_minor_arrow_acceptance_survives_translation_only(self):
        f=fixture();s=prepared(f);p=f['primitives'];sample=next(s for s in p['samples'] if s['key']=='edge:0')
        original=dimension_geometry(s,sample,0,set());loss=next(c for c in original['conflicts'] if c['code']=='OWN_ARROW_MASK')
        m=next(m for r in s['visibleStudy']['samples'] if r['key']=='edge:0' for m in r['measurements'] if m['pdfPath']==loss['path'])
        acceptance=dict(key='edge:0',primitiveDigest=p['digest'],pdfPath=loss['path'],removedAreaFraction=m['removedAreaFraction'],basis='USER_ACCEPTED_SHORT_DIMENSION_CLIPPING')
        accepted=_accepted_paths([acceptance],p,s['visibleStudy'])
        self.assertFalse(dimension_geometry(s,sample,250,accepted)['conflicts'])
        acceptance['removedAreaFraction']+=.001
        with self.assertRaises(ContractError):_accepted_paths([acceptance],p,s['visibleStudy'])

    def test_pool_keeps_inherited_obstacles_and_records_blockers_deterministically(self):
        f=fixture();args=[f[k] for k in ('scene','primitives','capture','ink','source')];before=copy.deepcopy(args)
        result=generate_joint_candidates(*args,options());self.assertEqual(before,args)
        self.assertFalse(result['productionEligible']);self.assertEqual(4,len(result['dimensionPointBindings']))
        self.assertEqual(result,generate_joint_candidates(*args,options()))
        self.assertTrue(any(c.get('obstacle')=='path:70' for r in result['candidates'] if r['key']=='leader:1' for c in r['conflicts']))
        self.assertTrue(all(c['requiresNativeReadback'] for c in result['candidates']))

    def test_missing_role_cannot_disappear_from_joint_pool(self):
        f=fixture();o=options();del o['dimensions']['edge:0']
        with self.assertRaisesRegex(ContractError,'JOINT_COVERAGE'):
            generate_joint_candidates(*(f[k] for k in ('scene','primitives','capture','ink','source')),o)

    def test_invalid_side_is_an_explicit_rejected_candidate(self):
        f=fixture();o=options();o['dimensions']['edge:0']=[-10000]
        result=generate_joint_candidates(*(f[k] for k in ('scene','primitives','capture','ink','source')),o)
        row=next(c for c in result['candidates'] if c['key']=='edge:0')
        self.assertEqual([{'code':'DIMENSION_SIDE'}],row['conflicts'])
