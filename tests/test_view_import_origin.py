"""A cached name from another project must not impersonate this renderer."""
import sys
import pytest


def test_cached_module_name_cannot_select_a_different_file(tmp_path, monkeypatch):
    from engineering.views import load
    first, second = tmp_path/'one.py', tmp_path/'two.py'
    first.write_text('VALUE = 1\n'); second.write_text('VALUE = 2\n')
    name = 'inventory_fixture_import_origin'
    monkeypatch.delitem(sys.modules,name,raising=False)
    assert load(name,first).VALUE == 1
    with pytest.raises(ValueError,match='IMPORT_ORIGIN'):
        load(name,second)
    sys.modules.pop(name,None)


def test_failed_import_does_not_remain_as_a_half_initialized_module(tmp_path):
    from engineering.views import load
    target=tmp_path/'bad.py'; target.write_text("raise RuntimeError('fixture')\n")
    name='inventory_fixture_failed_import'
    with pytest.raises(RuntimeError):
        load(name,target)
    assert name not in sys.modules

