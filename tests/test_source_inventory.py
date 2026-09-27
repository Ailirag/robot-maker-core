"""A declared source set works without Git and rejects accidental local inputs."""
import json
from pathlib import Path
import pytest


def test_only_declared_paths_are_enumerated_in_stable_order(tmp_path):
    from engineering.source_inventory import FileInventory
    for name in ('parts/z/item.stl', 'parts/a/item.stl', 'parts/local.stl'):
        path = tmp_path/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture')
    inventory = FileInventory(tmp_path, ['parts/z/item.stl', 'parts/a/item.stl'])
    assert [p.relative_to(tmp_path).as_posix() for p in inventory.files('parts','*.stl')] == [
        'parts/a/item.stl', 'parts/z/item.stl']
    assert not inventory.contains(tmp_path/'parts/local.stl')
    assert inventory.contains(tmp_path/'parts/a/item.stl')
    assert not (tmp_path/'.git').exists()
    (tmp_path/'parts/a/item.stl').unlink()
    with pytest.raises(ValueError, match='MISSING'):
        list(inventory.files('parts','*.stl'))


@pytest.mark.parametrize('paths', [
    ['../escape'], ['/absolute'], ['C:/outside'], ['a\\b'], ['a//b'], ['a/./b'],
    ['.git/config'], ['a','.git/config'], ['same','same'], [1], 'not-a-list'])
def test_invalid_or_ambiguous_declarations_refuse(tmp_path, paths):
    from engineering.source_inventory import FileInventory
    with pytest.raises(ValueError, match='INVENTORY'):
        FileInventory(tmp_path, paths)


def test_missing_manifest_and_undeclared_read_are_not_discovery_fallbacks(tmp_path):
    from engineering.source_inventory import FileInventory
    with pytest.raises(ValueError, match='INVENTORY'):
        FileInventory.load(tmp_path, 'inputs.json')
    (tmp_path/'inputs.json').write_text(json.dumps({'schema':'engineering-source-inventory-v1','paths':[]}))
    inventory = FileInventory.load(tmp_path, 'inputs.json')
    (tmp_path/'local.txt').write_text('unregistered')
    with pytest.raises(ValueError, match='UNDECLARED'):
        inventory.require('local.txt')

