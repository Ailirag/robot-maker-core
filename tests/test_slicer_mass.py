import io,zipfile
import numpy as np
import pytest
from engineering.slicer_mass import deposition, project_masses

def test_mass_and_com_exclude_support_brim_and_unretraction():
    g="""G21
G90
M83
G1 X0 Y0 Z0
; start printing object, unique label id: 1
; FEATURE: Outer wall
G1 X10 E2
G1 E-1
G1 E1
; FEATURE: Brim
G1 Y10 E4
; FEATURE: Sparse infill
G1 X0 E2
; stop printing object, unique label id: 1
; FEATURE: Support
G1 X10 E5
"""
    r=deposition(g,diameter_mm=2,density_g_cm3=1)
    assert r["objects"]["1"]["mass_g"]==pytest.approx(4*np.pi/1000)
    assert r["objects"]["1"]["com_bed_mm"]==pytest.approx([5,5,0])
    assert r["excluded_mass_g"]==pytest.approx(9*np.pi/1000)

def test_absolute_extrusion_and_reset():
    g="""G90
M82
G1 X0 Y0 Z0
G92 E100
; start printing object, unique label id: 7
; FEATURE: Inner wall
G1 X10 E102
G1 E101
G1 E102
G92 E0
G1 X20 E2
"""
    assert deposition(g,diameter_mm=2,density_g_cm3=1)["objects"]["7"]["mass_g"]==pytest.approx(4*np.pi/1000)

@pytest.mark.parametrize("line",["G20","M200 D1","G2 X10 Y0 R5 E2","T1"])
def test_unsupported_modal_gcode_refused(line):
    with pytest.raises(ValueError):
        deposition("G90\nM83\nG1 X0 Y0 Z0\n"+line,diameter_mm=2,density_g_cm3=1)

def test_ambiguous_model_deposition_is_not_assigned_to_arbitrary_part():
    with pytest.raises(ValueError,match="object"):
        deposition("G90\nM83\nG1 X0 Y0 Z0\n; FEATURE: Outer wall\nG1 X10 E1",
                   diameter_mm=2,density_g_cm3=1)
