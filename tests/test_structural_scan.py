"""Independent shapes exercise screening, not the generator's assertions."""
import math
import pytest
from build123d import Box, Compound, Cylinder, Pos, Rot
from engineering.structural_scan import scan_sections, scan_contact


def neck(width=1.4, depth=2.4):
    return (Pos(0, 0, -4)*Box(12, 12, 4)
            + Box(width, depth, 5) + Pos(0, 0, 4)*Box(12, 12, 4))


@pytest.mark.parametrize('rotation', [(0,0,0), (90,0,0), (0,90,0), (0,0,37)])
def test_small_neck_found_without_declaring_a_probe(rotation):
    result = scan_sections(Rot(*rotation)*neck())
    assert result['status'] == 'REVIEW_REQUIRED'
    assert any(abs(f['area_mm2']-3.36) < .01 for f in result['findings'])
    assert result['strength_n'] is None and result['obtained_by']
    assert result['physical_approval'] is False


def test_full_section_does_not_get_small_section_finding():
    assert scan_sections(neck(4, 4))['findings'] == []


def test_short_neck_between_uniform_samples_is_found_at_feature_interval():
    shape = (Pos(0,0,-2)*Box(12,12,4) + Pos(0,0,.15)*Box(1.4,2.4,.5)
             + Pos(0,0,2.3)*Box(12,12,4))
    assert any(abs(f['area_mm2']-3.36)<.01 for f in scan_sections(shape)['findings'])


def test_disconnected_body_is_not_silently_scanned_as_one_part():
    with pytest.raises(ValueError, match='one valid solid'):
        scan_sections(Compound(children=[Box(2,2,2), Pos(10,0,0)*Box(2,2,2)]))


def test_exhausted_sampling_budget_refuses_partial_success(monkeypatch):
    from engineering import structural_scan as S
    monkeypatch.setattr(S, 'MAX_PLANES', 2)
    with pytest.raises(ValueError, match='sampling budget'):
        S.scan_sections(neck())


def curved_joint(end):
    parent = Pos(0,0,19.5)*Cylinder(92,3) - Pos(0,0,19.5)*Cylinder(89.6,4)
    foot = Pos((61+end)/2,50,14.5)*Box(end-61,8,13)
    return parent, foot


def test_partial_foot_on_curve_is_detected_and_extension_passes():
    parent, foot = curved_joint(math.sqrt(89.6**2-50**2)+.5)
    result = scan_contact(parent, foot, axis=0)
    assert result['status'] == 'REVIEW_REQUIRED'
    assert .56 < result['covered_fraction'] < .62
    assert result['expected_projection_mm2'] == 24
    assert result['method'] == 'sampled_projection_with_minimum_depth'
    parent, foot = curved_joint(80)
    assert scan_contact(parent, foot, axis=0)['status'] == 'SCREENED'


def test_internal_contact_gap_is_not_hidden_by_bounding_box():
    parent = Box(4,8,4) - Box(5,4,3)
    foot = Box(4,8,2)
    result = scan_contact(parent, foot, axis=0)
    assert result['status'] == 'REVIEW_REQUIRED'
    assert result['covered_fraction'] == .5


def test_tangent_or_disconnected_contact_is_a_finding():
    result = scan_contact(Box(4,8,2), Pos(4,0,0)*Box(4,8,2), axis=0)
    assert result['covered_fraction'] == 0
    assert result['status'] == 'REVIEW_REQUIRED'


@pytest.mark.parametrize('axis', [-1,3,True,'x'])
def test_invalid_contact_axis_refused(axis):
    with pytest.raises(ValueError, match='axis'):
        scan_contact(Box(4,8,2), Box(4,8,2), axis=axis)


def test_full_footprint_with_paper_thin_overlap_still_requires_review():
    result = scan_contact(Box(4,8,2), Pos(3.9,0,0)*Box(4,8,2), axis=0)
    assert result['covered_fraction'] == 1
    assert result['adequate_depth_fraction'] == 0
    assert result['status'] == 'REVIEW_REQUIRED'


def test_contact_budget_exhaustion_is_not_an_empty_finding_list(monkeypatch):
    from engineering import structural_scan as S
    monkeypatch.setattr(S, 'MAX_RAYS', 2)
    with pytest.raises(ValueError, match='sampling budget'):
        S.scan_contact(Box(4,8,2), Box(4,8,2), axis=0)
