"""The exchange example is synthetic and cannot authorize a robot candidate."""
from copy import deepcopy
import math

import pytest


def observation():
    return {'valid':True, 'solids':1, 'volume_mm3':6000-120*math.pi,
            'bbox_mm':[10,20,30], 'axis_is_empty':True, 'wall_is_material':True}


def test_exchange_contract_has_analytic_expectations():
    from engineering import runtime_smoke as S
    assert S.problems(observation()) == []


@pytest.mark.parametrize('field,bad', [('valid',False),('valid','true'),('solids',2),
    ('volume_mm3',6000),('bbox_mm',[10,20,31]),('axis_is_empty',False),('wall_is_material',False)])
def test_bad_brep_or_missing_bore_refuses(field,bad):
    from engineering import runtime_smoke as S
    actual=observation(); actual[field]=bad
    assert S.problems(actual)


def test_export_never_reuses_an_existing_directory(tmp_path):
    from engineering import runtime_smoke as S
    (tmp_path/'owner.txt').write_text('keep')
    with pytest.raises(ValueError,match='EXISTS'):
        S.export(tmp_path)
    assert (tmp_path/'owner.txt').read_text() == 'keep'
