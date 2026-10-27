"""Geometric family precedes specialized machining proof and never authorizes it."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from test_modeling_independent_plate import plate, swept_section
from test_native_geometry import prism
from test_native_facts import read
from zijinhua_tekla.part_drawing.native_expressions import build_native_expression_requirements
from zijinhua_tekla.part_drawing.native_geometry import audit_native_geometry
from zijinhua_tekla.part_drawing.geometric_types import classify_shape


L_SECTION = [[0,0],[100,0],[100,60],[90,60],[90,10],[0,10]]
U_SECTION = [[0,0],[100,0],[100,60],[90,60],[90,10],[10,10],[10,60],[0,60]]


def shaped(section):
    raw = plate()
    solid = swept_section(section)
    raw['parts'][0].update(rawSolid=read(solid),finalSolid=read(copy.deepcopy(solid)))
    return raw


def audit(raw):
    return audit_native_geometry(raw)['parts'][0]


class GeometricPartTypeTests(unittest.TestCase):
    def test_commands_and_profile_names_do_not_determine_geometric_family(self):
        for command in ('ContourPlate','Beam','PolyBeam'):
            for profile in ('PL10*60','CUSTOM'):
                raw = plate(command);raw['parts'][0]['profile']=read(profile)
                result = audit(raw)
                with self.subTest(command=command,profile=profile):
                    self.assertEqual('FLAT_PLATE',result['partType']['type'])
                    self.assertEqual('CLASSIFIED',result['partType']['status'])
                    self.assertEqual('MATCHED',result['specializedGeometryStatus'])
                    self.assertEqual(result['partType'],classify_shape(result['commonShapeFacts'],.1))

    def test_spatial_l_and_u_are_folded_but_planar_l_outline_is_flat(self):
        for section in (L_SECTION,U_SECTION):
            result = audit(shaped(section))
            self.assertEqual('BENT_PLATE',result['partType']['type'],result)
            self.assertAlmostEqual(10,result['partType']['thickness'])
            self.assertEqual('TYPE_ROUTING',result['blockedStage'])
            self.assertEqual('NOT_RUN',result['specializedGeometryStatus'])
            self.assertEqual('BLOCKED',build_native_expression_requirements(shaped(section))['parts'][0]['status'])
        raw=plate();solid=prism(L_SECTION)
        raw['parts'][0].update(rawSolid=read(solid),finalSolid=read(copy.deepcopy(solid)))
        self.assertEqual('FLAT_PLATE',audit(raw)['partType']['type'])

    def test_nonflat_type_never_calls_flat_plate_specialized_proof(self):
        with patch('zijinhua_tekla.part_drawing.native_geometry._outline',side_effect=AssertionError('wrong route')):
            result=audit(shaped(L_SECTION))
        self.assertEqual('BENT_PLATE',result['partType']['type'])
        self.assertEqual('PART_TYPE_ROUTE_UNSUPPORTED',result['issues'][-1]['code'])

    def test_classified_flat_still_needs_final_geometry_and_profile_consistency(self):
        for mutation in ('profile','final'):
            raw=plate()
            if mutation=='profile':raw['parts'][0]['profile']=read('PL12*60')
            else:raw['parts'][0]['finalSolid']['value']['faces']['value'].pop()
            result=audit(raw)
            self.assertEqual('FLAT_PLATE',result['partType']['type'])
            self.assertEqual('SPECIALIZED_GEOMETRY',result['blockedStage'])
            self.assertEqual('BLOCKED',build_native_expression_requirements(raw)['parts'][0]['status'])

    def test_missing_faces_curvature_and_variable_thickness_are_not_guessed_as_bends(self):
        for mode in ('missing','curved','absent'):
            raw=shaped(L_SECTION);part=raw['parts'][0]
            if mode=='missing':part['rawSolid']['value']['faces']['value'].pop()
            if mode=='curved':part['rawSolid']['value']['edges']['value'][0]['apiType']='EDGE_TYPE_CURVED_SURFACE'
            if mode=='absent':part.pop('rawSolid')
            self.assertEqual('UNKNOWN',audit(raw)['partType']['type'])
        wedge=audit(shaped([[0,0],[100,0],[100,15],[0,10]]))
        self.assertEqual('PRISMATIC_SECTION',wedge['partType']['type'])
        self.assertEqual('NOT_RUN',wedge['specializedGeometryStatus'])

    def test_raw_nonflat_geometry_overrides_plate_name_and_planar_contour(self):
        raw=plate('ContourPlate')
        changed=swept_section(L_SECTION)
        raw['parts'][0].update(rawSolid=read(changed),finalSolid=read(copy.deepcopy(changed)))
        self.assertEqual('BENT_PLATE',audit(raw)['partType']['type'])

    def test_geometry_classification_is_rigid_transform_invariant(self):
        raw=shaped(U_SECTION)
        # Rigid 90-degree rotation plus translation, including native frames/normals.
        point=lambda p:[-p[1]+200,p[0]-50,p[2]+30]
        vector=lambda p:[-p[1],p[0],p[2]]
        part=raw['parts'][0]
        for key in ('rawSolid','finalSolid'):
            solid=part[key]['value']
            for f in solid['faces']['value']:
                f['normal']['value']=vector(f['normal']['value'])
                for loop in f['loops']['value']:loop['vertices']['value']=[point(p) for p in loop['vertices']['value']]
            for edge in solid['edges']['value']:
                for end in ('start','end'):edge[end]=point(edge[end])
        for key in ('coordinateSystem','dstvCoordinateSystem'):
            frame=part[key]['value'];frame['origin']=point(frame['origin'])
            for axis in ('axisX','axisY'):frame[axis]=vector(frame[axis])
        result=audit(raw)
        self.assertEqual('BENT_PLATE',result['partType']['type'])
        self.assertAlmostEqual(10,result['partType']['thickness'])

    def test_real_bevels_and_slots_are_features_of_flat_stock(self):
        root=Path(__file__).parent/'fixtures/native_sections'
        for name in ('beam_two_bevels','target_slot_nc'):
            raw=json.loads((root/(name+'.json')).read_text('utf-8'))
            batch=build_native_expression_requirements(raw,nc_tolerance_mm=.01,max_resolution_mm=.01)
            self.assertEqual('FLAT_PLATE',batch['parts'][0]['partType']['type'])
            self.assertEqual('READY',batch['parts'][0]['status'])
            self.assertTrue(batch['parts'][0]['sectionRequirements'])


if __name__=='__main__':unittest.main()
