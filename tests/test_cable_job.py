import json
from engineering import cable_job as J
def test_job_missing_topology_cannot_be_called_complete(tmp_path):
    def v(n):return {"value":n,"obtained_by":"fixture"}
    spec=dict(id="wire",points=v([[0,0,0],[20,0,0]]),diameter_mm=v(1),bend_radius_mm=v(2),
        clearance_mm=v(0),termination_mm=v([1,1]),service_mm=v(3),available_mm=v(None),
        endpoints_confirmed=True,obstacles_complete=True)
    source=tmp_path/"input.json";out=tmp_path/"report.json"
    payload=dict(schema="cable-job-v1",cables=[spec],required_ids=["wire"],obstacles=[])
    source.write_text(json.dumps(payload))
    assert J.run(source,out)["status"]=="ACTION_REQUIRED"
    payload["topology_complete"]=True
    source.write_text(json.dumps(payload))
    report=J.run(source,out)
    assert report["status"]=="CALCULATED"
    assert report["cables"][0]["cut_length_mm"]==25
    assert report["physical_approval"] is False


def test_incomplete_group_cannot_leave_member_cut_lengths_valid(tmp_path):
    def v(n):return {'value':n,'obtained_by':'fixture'}
    cable=dict(id='wire',points=v([[0,0,0],[20,0,0]]),diameter_mm=v(1),bend_radius_mm=v(2),
        clearance_mm=v(0),termination_mm=v([1,1]),service_mm=v(3),available_mm=v(None),
        endpoints_confirmed=True,obstacles_complete=True)
    source=tmp_path/'job.json'
    source.write_text(json.dumps(dict(schema='cable-job-v1',cables=[cable],required_ids=['wire'],
        obstacles=[],topology_complete=True,bundles=[dict(id='bundle',members=['wire'])])))
    report=J.run(source,tmp_path/'result.json')
    assert report['status']=='ACTION_REQUIRED'
    assert report['bundles'][0]['status']=='INCOMPLETE'
    assert report['cables'][0]['cut_length_mm'] is None
