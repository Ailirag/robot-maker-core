"""Analytic fixtures independent of any product feature inventory."""
import json
import pytest
from build123d import Box, Compound, Cylinder, Pos, Rot, export_step
from engineering import weakness_scan as W


def bridge(width=2.4):
    return Pos(-7,0,0)*Box(8,10,10) + Box(8,width,width) + Pos(7,0,0)*Box(8,10,10)


def test_large_enclosure_is_not_skipped_by_default_voxel_budget():
    report=W.scan_necks(Box(200,200,62))
    assert report['coverage']['complete']
    assert report['grid']['cells']>20000000
    assert report['grid']['pitch_mm']==.5


def test_large_plate_preserves_surface_sampling_resolution():
    report=W.scan_walls(Box(200,200,2))
    assert report['sampled_points']>250000
    assert report['coverage']['complete']
    assert report['policy']['max_surface_pitch_mm']==1.0


@pytest.mark.parametrize("rotation", [(0,0,0),(23,37,19),(71,13,48)])
def test_large_flat_and_oblique_walls_are_measured(rotation):
    report = W.scan_walls(Rot(*rotation)*Box(14,12,.8))
    assert report["status"] == "REVIEW_REQUIRED"
    assert any(abs(f["min_thickness_mm"]-.8)<.001 for f in report["findings"])
    assert report["coverage"]["complete"]
    assert report["sampled_faces"] == 6


def test_curved_wall_and_hole_ligament():
    shape = Cylinder(6,8)-Cylinder(5.2,10)
    report = W.scan_walls(Rot(32,15,17)*shape)
    assert any(abs(f["min_thickness_mm"]-.8)<.01 for f in report["findings"])


def test_cavity_is_not_counted_as_wall_material():
    shape = Box(12,10,8)-Box(8,6,10)
    report = W.scan_walls(shape)
    assert report["findings"] == []
    assert report["minimum_measured_mm"] == pytest.approx(2,abs=.01)


@pytest.mark.parametrize("rotation", [(0,0,0),(23,37,19)])
def test_undeclared_bridge_splits_thick_cores(rotation):
    report = W.scan_necks(Rot(*rotation)*bridge())
    assert report["coverage"]["complete"]
    assert any(f["kind"]=="NARROW_CONNECTION" for f in report["findings"])


def test_full_connection_and_solid_block_have_no_weak_neck():
    for shape in (bridge(5),Box(12,12,12)):
        assert W.scan_necks(shape)["findings"] == []


def test_short_connector_is_found_without_a_hand_written_probe():
    shape=Pos(-4.4,0,0)*Box(8,10,10)+Box(1,2.4,2.4)+Pos(4.4,0,0)*Box(8,10,10)
    assert W.scan_necks(shape)["findings"]


def test_combined_report_is_deterministic_and_never_grants_strength():
    shape=Box(6,6,.8)
    a,b=W.scan(shape),W.scan(shape)
    assert a==b
    assert a["status"]=="REVIEW_REQUIRED"
    assert a["strength_n"] is None and a["obtained_by"]
    assert a["physical_approval"] is False
    assert a["findings"] and all(f["bounds_mm"] for f in a["findings"])


@pytest.mark.parametrize("fault", ["multiple","surface_budget","voxel_budget"])
def test_invalid_or_incomplete_scan_cannot_pass(monkeypatch,fault):
    shape=Box(6,6,6)
    if fault=="multiple":shape=Compound(children=[shape,Pos(20,0,0)*shape])
    elif fault=="surface_budget":monkeypatch.setattr(W,"MAX_SURFACE_SAMPLES",1)
    else:monkeypatch.setattr(W,"MAX_VOXELS",1)
    with pytest.raises(ValueError):
        W.scan(shape)


@pytest.mark.parametrize("pitch", [0,-1,True,float("nan"),float("inf"),2])
def test_cannot_silently_coarsen_sampling(pitch):
    with pytest.raises(ValueError):
        W.scan(Box(4,4,4),voxel_pitch_mm=pitch)


def test_cli_reads_actual_step_and_reports_unresolved(tmp_path):
    step=tmp_path/"wall.step"
    export_step(Rot(23,17,11)*Box(8,8,.8),step)
    output=tmp_path/"result.json"
    assert W.main(["--step",str(step),"--output",str(output)])==2
    report=json.loads(output.read_text(encoding="utf-8"))
    assert report["step_sha256"] and report["findings"]
    with pytest.raises(FileExistsError):
        W.main(["--step",str(step),"--output",str(output)])


def test_subgrid_body_requests_refinement_instead_of_passing():
    report=W.scan_necks(Box(6,6,.1))
    assert report["coverage"]["complete"] is False
    assert report["findings"][0]["kind"]=="GRID_UNRESOLVED"


def test_previous_coarse_display_mesh_does_not_change_measurement():
    shape=Rot(31,22,17)*(Cylinder(7,5)-Cylinder(6.2,7))
    shape.tessellate(2,1)
    first=W.scan_walls(shape)
    shape.tessellate(.001,.01)
    assert W.scan_walls(shape)==first


def test_ray_intersector_failure_cannot_be_a_clean_report(monkeypatch):
    monkeypatch.setattr(W._Rays,"crossings",
                        lambda *a: (_ for _ in ()).throw(ValueError("CAD failed")))
    with pytest.raises(ValueError,match="CAD failed"):
        W.scan(Box(6,6,6))


def test_explicit_budget_preserves_resolution_and_default_refusal(monkeypatch):
    monkeypatch.setattr(W,"MAX_VOXELS",1000)
    with pytest.raises(ValueError,match="budget"):
        W.scan_necks(Box(6,6,6))
    result=W.scan_necks(Box(6,6,6),max_voxels=10000)
    assert result["coverage"]["complete"]
    assert result["grid"]["pitch_mm"]==.5
    assert result["grid"]["cells"]>1000
    assert result["policy"]["max_voxels"]==10000
    assert W.scan(Box(6,6,6),max_voxels=10000)["coverage"]["complete"]

@pytest.mark.parametrize("budget",[0,-1,True,1.5,float("inf"),float("nan"),"64000000"])
def test_invalid_resource_limit_is_rejected(budget):
    with pytest.raises(ValueError,match="budget"):
        W.scan_necks(Box(2,2,2),max_voxels=budget)
