"""G-code evidence must be single-pass and bounded by regions, not move count."""
import tracemalloc
import pytest
from engineering import print_risks as R
from tests.test_print_risks import gcode, roof

def test_iterator_reads_only_as_far_as_requested():
    def lines():
        yield from gcode().splitlines(True)
        raise RuntimeError("sentinel")
    stream=R.iter_toolpaths(lines())
    assert next(stream)["length_mm"]==pytest.approx(3)
    with pytest.raises(RuntimeError,match="sentinel"): next(stream)

def test_streamed_evidence_matches_existing_local_support_and_roles():
    regions=R.geometry_risks(roof())["regions"]
    text=gcode()+gcode(role="Bridge")+gcode(role="Overhang wall")
    rows=R.toolpaths(text)
    evidence=R.toolpath_evidence(iter(rows),{"1":"part"},{"part":regions},top_gap_mm=.2)
    assert evidence["part"]["toolpath"]==R._summary(rows)
    assert evidence["part"]["regions"]=={
        r["id"]:R.support_evidence(r,rows,top_gap_mm=.2) for r in regions}

def test_stream_does_not_keep_one_entry_per_deposition():
    region={"id":"r","bounds_mm":[[0,0,1],[3,3,1]]}
    def segments():
        for i in range(50000):
            yield dict(a=[0,1,.8],b=[2,1,.8],role="Support interface",object="1",length_mm=2.)
    tracemalloc.start()
    try:
        evidence=R.toolpath_evidence(segments(),{"1":"part"},{"part":[region]},top_gap_mm=.2)
        _,peak=tracemalloc.get_traced_memory()
    finally: tracemalloc.stop()
    assert peak<2_000_000
    assert evidence["part"]["regions"]["r"]["interface_length_mm"]==100000.
    assert evidence["part"]["regions"]["r"]["interface_z_mm"]==[.8]

@pytest.mark.parametrize("labels", [{"2":"other"},{"1":"part","2":"missing"}])
def test_stream_still_rejects_unknown_or_missing_objects(labels):
    with pytest.raises(ValueError,match="PRINTABILITY"):
        R.toolpath_evidence(R.iter_toolpaths(gcode()),labels,
            {name:[] for name in labels.values()},top_gap_mm=.2)
