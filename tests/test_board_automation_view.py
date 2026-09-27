"""Calculation receipts remain useful and safe inside a standalone manual."""
import json
import re
import unittest
from engineering import board_model,board_route,board_search
from engineering.instructions import board_solder
from test_board_automation import sample

class AutomationViewTests(unittest.TestCase):
    def test_report_is_embedded_and_unknowns_are_actionable(self):
        from engineering.instructions import board_automation
        b,f=sample();b['physical']['clearance_mm']={'value':None,'unit':'mm','obtained_by':'Measure <gap> & record it.'}
        r=board_search.solve(b,f,max_nodes=100)
        html=board_automation.render(b,r)
        self.assertIn('INPUTS_INCOMPLETE',html)
        self.assertIn('Measure &lt;gap&gt; &amp; record it.',html)
        self.assertIn('download="board-model.json"',html)
        self.assertIn('download="board-search.json"',html)
        record=json.loads(re.search(r'class="board-search-result">(.*?)</script>',html).group(1))
        self.assertEqual(record,r)
        self.assertNotIn('<gap>',html)
    def test_download_names_distinguish_board_and_stage(self):
        from engineering.instructions import board_automation
        b,f=sample();r=board_search.solve(b,f,max_nodes=1000)
        html=board_automation.render(b,r,filename_prefix='stage-one-board-a')
        self.assertIn('download="stage-one-board-a-model.json"',html)
        with self.assertRaisesRegex(ValueError,'BOARD_AUTOMATION'):
            board_automation.render(b,r,filename_prefix='../wrong')

    def test_moved_placement_drives_operation_targets_without_old_coordinates(self):
        b,f=sample();r=board_search.solve(b,f,max_nodes=1000)
        layout=board_route.workspace(b,r['best']['placements'],r['best']['links'])
        op=dict(id='place-r',kind='place',source_step=1,title='Place resistor',action='Use named holes.',
                side='front',refs=['R_A'],links=[],material='Synthetic resistor',check='Match pins.')
        self.assertEqual(set(board_solder.targets(layout,op)['pads']),{'1,0','1,1'})
        b['components']['R_A']['fixed']={'at':[3,2],'rotation':90}
        moved=board_search.solve(b,f,max_nodes=1000)
        other=board_route.workspace(b,moved['best']['placements'],moved['best']['links'])
        self.assertEqual(set(board_solder.targets(other,op)['pads']),{'3,2','2,2'})
        self.assertEqual(op['action'],'Use named holes.')

if __name__=='__main__': unittest.main()
