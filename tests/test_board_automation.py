"""Portable placement, independent physical/electrical checks and bounded search."""
from copy import deepcopy
import json
import unittest
from engineering import board_model as model, board_route as route, board_search as search
from engineering.facts import FactManifest

def sample():
    values={'pitch':1.0,'margin':0.5,'width':4.0,'height':3.0,'clearance':0.0,
            'ceiling':2.0,'envelope':[-0.2,-0.2,0,0.2,1.2,1.0],'fit':True}
    facts=FactManifest({'parameters':{k:dict(value=v,unit='bool' if k=='fit' else 'mm',
        provenance='SYNTHETIC',datum='local pin 1; board hole 0,0',scope='sample',source='constructed software fixture')
        for k,v in values.items()}})
    def q(k): return dict(fact_id=k,unit='bool' if k=='fit' else 'mm',
        allowed_provenance=['SYNTHETIC'],require_datum=True,scope='sample')
    board=dict(schema=model.SCHEMA,grid=dict(columns=4,rows=3),keepouts=[],
        physical={k:q(v) for k,v in dict(pitch_mm='pitch',edge_x_mm='margin',edge_y_mm='margin',
            width_mm='width',height_mm='height',clearance_mm='clearance',max_height_mm='ceiling',keepout_review='fit').items()},
        footprints={'pair':dict(pins={'1':[0,0],'2':[0,1]},
            physical=dict(envelope_mm=q('envelope'),pin_fit=q('fit'),pitch_mm=q('pitch')))},
        components={r:dict(footprint='pair',nets={'1':'A','2':'B'},rotations=[0,90],
            fixed=dict(at=[0,0],rotation=0) if r=='J_A' else None,region=None) for r in ('J_A','R_A')})
    return board,facts

class ModelTests(unittest.TestCase):
    def test_exact_local_rotation_and_net_mapping(self):
        b,f=sample(); p={'J_A':{'at':[0,0],'rotation':0},'R_A':{'at':[3,2],'rotation':90}}
        self.assertEqual(model.pins(b,p)['R_A'],{'1':(3,2),'2':(2,2)})
        self.assertEqual(model.pin_nets(b)[('R_A','2')],'B')
        self.assertEqual(model.audit(b,p,f)['status'],'VALID')
    def test_rejects_malformed_contract_not_guessed_defaults(self):
        cases=[lambda b:b['grid'].update(columns=True),
            lambda b:b['footprints']['pair']['pins'].update({'3':[0.5,0]}),
            lambda b:b['components']['J_A']['nets'].pop('2'),
            lambda b:b['components']['J_A'].update(footprint='absent'),
            lambda b:b['components']['J_A'].update(rotations=[45]),
            lambda b:b['components']['J_A'].update(rotations=[0,0]),
            lambda b:b['components']['J_A'].update(region=[2,0,1,1]),
            lambda b:b['physical']['pitch_mm'].update(unit='cm'),
            lambda b:b['physical']['pitch_mm'].update(require_datum=False),
            lambda b:b['physical']['pitch_mm'].update(allowed_provenance=[]),
            lambda b:b['physical'].update(pitch_mm={'value':1,'unit':'mm','obtained_by':'guess'}),
            lambda b:b['physical'].update(pitch_mm={'value':None,'unit':'mm','obtained_by':''})]
        for change in cases:
            b,_=sample();change(b)
            with self.subTest(change=change),self.assertRaisesRegex(ValueError,'BOARD_MODEL'):
                model.validate(b)
    def test_independent_placement_checks(self):
        cases=[({'at':[0,0],'rotation':0},'PAD_COLLISION'),
               ({'at':[4,0],'rotation':0},'GRID_BOUNDS'),
               ({'at':[2,2],'rotation':0},'GRID_BOUNDS'),
               ({'at':[2,0],'rotation':180},'ROTATION')]
        for part,code in cases:
            b,f=sample();p={'J_A':b['components']['J_A']['fixed'],'R_A':part}
            with self.subTest(code=code): self.assertIn(code,{x['code'] for x in model.audit(b,p,f)['issues']})
        b,f=sample();p={'J_A':{'at':[1,0],'rotation':0},'R_A':{'at':[3,0],'rotation':0}}
        self.assertIn('FIXED',{x['code'] for x in model.audit(b,p,f)['issues']})
    def test_body_overlap_even_when_pins_do_not_overlap(self):
        b,f=sample();f._parameters['envelope']['value']=[-.6,-.2,0,.6,1.2,1]
        p={'J_A':b['components']['J_A']['fixed'],'R_A':{'at':[1,0],'rotation':0}}
        self.assertIn('BODY_COLLISION',{x['code'] for x in model.audit(b,p,f)['issues']})
    def test_physical_missing_scope_units_provenance_datum_fail_closed(self):
        for update,code in [({'value':None},'MISSING'),({'unit':'cm'},'WRONG_UNIT'),
                ({'scope':'other'},'WRONG_SCOPE'),({'provenance':'ESTIMATE'},'INSUFFICIENT_PROVENANCE'),
                ({'datum':None},'AMBIGUOUS_DATUM'),({'datum':''},'AMBIGUOUS_DATUM')]:
            b,f=sample();f._parameters['pitch'].update(update)
            result=model.physical_inputs(b,f)
            with self.subTest(code=code):
                self.assertEqual(result['status'],'INPUTS_INCOMPLETE')
                self.assertIn(code,{x['code'] for x in result['issues']})
        b,f=sample();b['footprints']['pair']['physical']['envelope_mm']={
            'value':None,'unit':'mm','obtained_by':'Measure from pin 1, including service access.'}
        result=model.physical_inputs(b,f)
        self.assertEqual(result['status'],'INPUTS_INCOMPLETE')
        self.assertIn('Measure from pin 1',json.dumps(result))
        self.assertIsNone(result['values']['footprints.pair.envelope_mm'])
    def test_bad_physical_values_never_enable_search(self):
        for key,value in [('pitch',True),('pitch',float('nan')),('pitch',0),('fit',False),
                ('envelope',[0,0,0,0,1,1]),('envelope',[0,0,0,1,1]),('clearance',-1)]:
            b,f=sample();f._parameters[key]['value']=value
            with self.subTest(key=key,value=value):
                self.assertEqual(model.physical_inputs(b,f)['status'],'INVALID')
    def test_regions_keepouts_and_height_are_constraints(self):
        b,f=sample();b['components']['R_A']['region']=[2,0,3,1];b['keepouts']=[[2,0,2,2]]
        p={'J_A':b['components']['J_A']['fixed'],'R_A':{'at':[2,1],'rotation':0}}
        codes={x['code'] for x in model.audit(b,p,f)['issues']}
        self.assertTrue({'REGION','KEEPOUT'}<=codes)
        f._parameters['ceiling']['value']=0.5
        self.assertIn('HEIGHT',{x['code'] for x in model.audit(b,p,f)['issues']})
    def test_logical_replay_does_not_claim_physical_acceptance(self):
        b,f=sample();b['physical']['clearance_mm']={'value':None,'unit':'mm','obtained_by':'Owner requirement.'}
        p={'J_A':b['components']['J_A']['fixed'],'R_A':{'at':[2,0],'rotation':0}}
        self.assertEqual(model.audit(b,p,f)['status'],'INPUTS_INCOMPLETE')
        self.assertEqual(model.audit(b,p)['status'],'LOGICAL_VALID')
        self.assertFalse(model.audit(b,p)['physical_release'])

class RouteTests(unittest.TestCase):
    def test_orthogonal_routes_and_insulated_fallback(self):
        pins={'A':{'1':(0,0)},'B':{'1':(2,2)},'X':{'1':(0,1),'2':(1,0)}}
        nets={('A','1'):'N',('B','1'):'N',('X','1'):None,('X','2'):None}
        links=route.connect(pins,nets,columns=3,rows=3)
        self.assertEqual(links,(('N',(0,0),(2,2),'jumper'),))
        result=route.check(pins,nets,links,columns=3,rows=3)
        self.assertEqual(result['status'],'VALID')
        self.assertNotIn((1,1),result['holes'])
    def test_independent_short_break_and_non_axis_checks(self):
        pins={'A':{'1':(0,0),'2':(2,0)},'B':{'1':(1,0),'2':(1,2)}}
        nets={('A','1'):'A',('A','2'):'A',('B','1'):'B',('B','2'):'B'}
        for links,code in [
            ((('A',(0,0),(2,0),'wire'),('B',(1,0),(1,2),'wire')),'SHORT'),
            ((('A',(0,0),(2,0),'jumper'),),'OPEN'),
            ((('A',(0,0),(2,2),'wire'),),'BARE_DIAGONAL'),
            ((('X',(0,0),(2,0),'jumper'),),'UNKNOWN_NET')]:
            with self.subTest(code=code):
                self.assertIn(code,{x['code'] for x in route.check(pins,nets,links,columns=3,rows=3)['issues']})
    def test_bare_crossing_is_short_but_insulated_crossing_is_not(self):
        pins={'A':{'1':(0,1),'2':(2,1)},'B':{'1':(1,0),'2':(1,2)}}
        nets={(r,k):r for r,v in pins.items() for k in v}
        links=(('A',(0,1),(2,1),'wire'),('B',(1,0),(1,2),'wire'))
        self.assertIn('SHORT',{x['code'] for x in route.check(pins,nets,links,columns=3,rows=3)['issues']})
        links=(links[0],('B',(1,0),(1,2),'jumper'))
        self.assertEqual(route.check(pins,nets,links,columns=3,rows=3)['status'],'VALID')
    def test_non_endpoint_pin_is_connected_by_bare_run(self):
        pins={'A':{'1':(0,0),'2':(1,0),'3':(2,0)}}
        nets={('A',k):'N' for k in pins['A']}
        result=route.check(pins,nets,(('N',(0,0),(2,0),'wire'),),columns=3,rows=1)
        self.assertEqual(result['status'],'VALID')
    def test_order_independent_and_keepout_avoids_bare_wire(self):
        pins={'A':{'1':(0,0)},'B':{'1':(2,0)}}
        nets={('A','1'):'N',('B','1'):'N'}
        links=route.connect(pins,nets,columns=3,rows=2,keepouts=[[1,0,1,0]])
        self.assertEqual(links[0][3],'jumper')
        self.assertEqual(links,route.connect(dict(reversed(list(pins.items()))),dict(reversed(list(nets.items()))),
            columns=3,rows=2,keepouts=[[1,0,1,0]]))
    def test_duplicate_pin_and_unknown_connection_type_refused(self):
        pins={'A':{'1':(0,0)},'B':{'1':(0,0)}}
        nets={('A','1'):'N',('B','1'):'N'}
        with self.assertRaisesRegex(ValueError,'BOARD_ROUTE'):
            route.connect(pins,nets,columns=3,rows=2)
        self.assertEqual(route.check({'A':{'1':(0,0),'2':(1,0)}},{('A','1'):'N',('A','2'):'N'},
            (('N',(0,0),(1,0),'magic'),),columns=3,rows=2)['status'],'INVALID')

class SearchTests(unittest.TestCase):
    def test_finds_repeatable_solution_and_checks_it_independently(self):
        b,f=sample();r=search.solve(b,f,max_nodes=1000)
        self.assertEqual(r['status'],'SOLVED')
        self.assertTrue(r['exhaustive'])
        self.assertFalse(r['physical_release'])
        self.assertEqual(r['best']['score'],[0,2])
        self.assertEqual(r['best']['placements']['R_A'],{'at':[1,0],'rotation':0})
        self.assertEqual(model.audit(b,r['best']['placements'],f)['status'],'VALID')
        pins=model.pins(b,r['best']['placements'])
        self.assertEqual(route.check(pins,model.pin_nets(b),r['best']['links'],**b['grid'])['status'],'VALID')
        self.assertEqual(r,search.solve(json.loads(json.dumps(b)),f,max_nodes=1000))
        b['components']=dict(reversed(list(b['components'].items())))
        b['components']['R_A']['rotations'].reverse()
        self.assertEqual(r,search.solve(b,f,max_nodes=1000))
    def test_limit_is_not_infeasibility_even_with_feasible_incumbent(self):
        b,f=sample()
        r=search.solve(b,f,max_nodes=1)
        self.assertEqual(r['status'],'SEARCH_LIMIT')
        self.assertFalse(r['exhaustive'])
        self.assertEqual(r['visited'],1)
        self.assertIsNone(r['best'])
        r=search.solve(b,f,max_nodes=10)
        self.assertEqual(r['status'],'SEARCH_LIMIT')
        self.assertIsNotNone(r['best'])
        self.assertFalse(r['optimal_within_model'])
    def test_exhaustive_infeasibility_and_missing_inputs_distinct(self):
        b,f=sample();b['components']['R_A']['region']=[0,0,0,1]
        self.assertEqual(search.solve(b,f,max_nodes=1000)['status'],'INFEASIBLE')
        b['physical']['pitch_mm']={'value':None,'unit':'mm','obtained_by':'Measure grid.'}
        r=search.solve(b,f,max_nodes=1000)
        self.assertEqual(r['status'],'INPUTS_INCOMPLETE')
        self.assertEqual(r['visited'],0)
    def test_fixed_rotation_must_be_preserved_and_no_silent_limit_coercion(self):
        b,f=sample()
        for budget in [True,0,-1,1.5]:
            with self.subTest(budget=budget),self.assertRaisesRegex(ValueError,'BOARD_SEARCH'):
                search.solve(b,f,max_nodes=budget)
        b['components']['R_A']['fixed']={'at':[3,2],'rotation':90}
        r=search.solve(b,f,max_nodes=1000)
        self.assertEqual(r['best']['placements']['R_A'],b['components']['R_A']['fixed'])
    def test_hash_tracks_consumed_input_but_not_unrelated_fact(self):
        b,f=sample();r=search.solve(b,f,max_nodes=1000)
        f._parameters['unused']={'value':999}
        self.assertEqual(r,search.solve(b,f,max_nodes=1000))
        f._parameters['margin']['value']=0.6
        self.assertNotEqual(r['input_sha256'],search.solve(b,f,max_nodes=1000)['input_sha256'])


class PhysicalBoundaryTests(unittest.TestCase):
    def test_grid_must_fit_recorded_board_and_envelope_must_include_pins(self):
        for key,value,code in [('width',2.0,'GRID_PHYSICAL_BOUNDS'),
                ('envelope',[-.2,-.2,0,.2,.5,1],'ENVELOPE_PINS')]:
            b,f=sample();f._parameters[key]['value']=value
            r=model.physical_inputs(b,f)
            with self.subTest(code=code):
                self.assertEqual(r['status'],'INVALID')
                self.assertIn(code,{x['code'] for x in r['issues']})
    def test_board_keepout_review_cannot_be_implicitly_assumed(self):
        b,f=sample();b['physical']['keepout_review']={'value':None,'unit':'bool','obtained_by':'Inspect mounts and extraction corridors.'}
        r=search.solve(b,f,max_nodes=100)
        self.assertEqual(r['status'],'INPUTS_INCOMPLETE')
        self.assertEqual(r['visited'],0)
    def test_large_input_is_refused_before_quadratic_router_work(self):
        pins={'X':{str(i):(i%200,i//200) for i in range(513)}}
        nets={('X',k):'N' for k in pins['X']}
        with self.assertRaisesRegex(ValueError,'BOARD_ROUTE'):
            route.connect(pins,nets,columns=200,rows=200)


class CommandTests(unittest.TestCase):
    def test_json_command_replays_inputs_and_preserves_outcome_exit_codes(self):
        import subprocess,sys,tempfile
        from pathlib import Path
        b,f=sample()
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);(d/'model.json').write_text(json.dumps(b),encoding='utf-8')
            (d/'facts.json').write_text(json.dumps({'parameters':f._parameters}),encoding='utf-8')
            command=[sys.executable,'-m','engineering.board_search','--model',str(d/'model.json'),'--facts',str(d/'facts.json')]
            for budget,code,status in [('1000',0,'SOLVED'),('1',2,'SEARCH_LIMIT')]:
                result=subprocess.run(command+['--max-nodes',budget],capture_output=True,text=True,encoding='utf-8')
                with self.subTest(status=status):
                    self.assertEqual(result.returncode,code,result.stderr)
                    self.assertEqual(json.loads(result.stdout)['status'],status)
            b['physical']['clearance_mm']={'value':None,'unit':'mm','obtained_by':'Declare clearance.'}
            (d/'model.json').write_text(json.dumps(b),encoding='utf-8')
            result=subprocess.run(command+['--max-nodes','1000'],capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(result.returncode,3,result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'],'INPUTS_INCOMPLETE')

if __name__=='__main__': unittest.main()
