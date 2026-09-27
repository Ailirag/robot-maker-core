"""All thin samples in a grouped wall finding need context, not just its minimum."""
import copy
from functools import lru_cache
import pytest
from build123d import Align,Box,Face,Wire,extrude
from engineering import weakness_scan as W

@lru_cache
def fixture():
    shape=extrude(Face(Wire.make_polygon(
        [(0,0,0),(8,0,0),(8,0,.1),(6,0,1),(0,0,1)],close=True)),
        amount=6,dir=(0,1,0))
    return shape,W.scan_walls(shape)

def replay(shape,report,**kwargs):
    from engineering import thin_sample_context as C
    return C.replay(shape,report,expected_identity=W.identity(),**kwargs)

def test_group_with_oblique_minimum_still_exposes_opposing_thin_material():
    from engineering import chord_context as C
    shape,report=fixture()
    before=copy.deepcopy(report)
    result=replay(shape,report)
    assert report==before
    mixed=[]
    for finding,row in zip(report["findings"],result["findings"]):
        assert row["id"]==finding["id"]
        assert sum(row["contexts"].values())==finding["thin_samples"]
        assert row["thin_samples"]==finding["thin_samples"]
        assert row["original_thin_subset_reproduced"] is True
        if C.inspect(shape,finding)["context"]=="OBLIQUE_EXIT" and row["contexts"]["OPPOSING_SURFACES"]:
            mixed.append(row)
    assert mixed
    assert result["coverage"]["findings"]==len(report["findings"])
    assert result["coverage"]["thin_samples"]==sum(f["thin_samples"] for f in report["findings"])
    assert result["requires_engineering_review"] is True
    assert result["physical_approval"] is False
    assert result["strength_n"] is None
    assert "recorded" in result["scope"]

@pytest.mark.parametrize("change",["count","bounds","minimum","missing_face","policy","coverage","schema","duplicate"])
def test_invalid_or_changed_report_fails_closed(change):
    shape,source=fixture()
    report=copy.deepcopy(source)
    f=report["findings"][0]
    if change=="count":f["thin_samples"]+=1
    elif change=="bounds":f["bounds_mm"][0][0]+=1
    elif change=="minimum":f["min_thickness_mm"]+=.2
    elif change=="missing_face":f["face"]=999
    elif change=="policy":report["policy"]["wall_limit_mm"]=2
    elif change=="coverage":report["coverage"]["complete"]=False
    elif change=="schema":report["schema"]="other"
    else:report["findings"].append(copy.deepcopy(f))
    with pytest.raises(ValueError,match="THIN_CONTEXT"):
        replay(shape,report)

def test_other_geometry_cannot_replay_a_bound_report():
    shape,report=fixture()
    with pytest.raises(ValueError,match="THIN_CONTEXT"):
        replay(Box(8,6,2,align=(Align.MIN,)*3),report)

def test_runtime_change_is_refused():
    from engineering import thin_sample_context as C
    shape,report=fixture()
    with pytest.raises(ValueError,match="THIN_CONTEXT"):
        C.replay(shape,report,expected_identity={})

def test_replay_budget_cannot_silently_skip_samples():
    shape,report=fixture()
    with pytest.raises(ValueError,match="THIN_CONTEXT.*budget"):
        replay(shape,report,max_surface_samples=1)

def test_empty_screen_is_context_only_and_reports_zero():
    shape=Box(4,4,4,align=(Align.MIN,)*3)
    report=W.scan_walls(shape)
    result=replay(shape,report)
    assert result["coverage"]["thin_samples"]==0
    assert result["findings"]==[]
    assert result["physical_approval"] is False
