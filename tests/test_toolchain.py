"""An environment must match its declared runtime contract before evidence."""
import copy
from pathlib import Path

import pytest

from engineering.build_composition import file_pin


@pytest.fixture
def environment(tmp_path):
    lock = 'fixture==1.2.3 --hash=sha256:' + 'a' * 64 + '\n'
    (tmp_path / 'requirements.lock').write_text(lock, encoding='utf-8', newline='\n')
    (tmp_path / 'contrib.lock').write_text('contrib==2.0 --hash=sha256:' + 'b'*64 + '\n', encoding='utf-8', newline='\n')
    (tmp_path / 'source.py').write_text('print("fixture")\n', encoding='utf-8', newline='\n')
    policy = {'schema': 'engineering-toolchain-v1', 'id': 'fixture-toolchain',
        'python': '3.14.3', 'platform': 'win32', 'machine': 'AMD64',
        'requirements': file_pin(tmp_path, 'requirements.lock'),
        'contrib_requirements': file_pin(tmp_path, 'contrib.lock'),
        'freecad': {'version': '1.1.1', 'python': '3.11.14', 'occ': '7.8.1'},
        'sdk_packages': {'fixture-compiler': '8.4.0'}, 'source_lf': ['source.py']}
    observed = {'python': '3.14.3', 'platform': 'win32', 'machine': 'AMD64',
        'isolated': True, 'pythonpath': None, 'packages': {'fixture': '1.2.3'},
        'contrib_packages': {'contrib': '2.0'},
        'freecad': dict(policy['freecad']), 'sdk_packages': dict(policy['sdk_packages'])}
    return tmp_path, policy, observed


def test_complete_environment_grants_no_physical_permission(environment):
    from engineering import toolchain as T
    report = T.diagnose(*environment)
    assert report['status'] == 'READY', report
    assert report['physical_approval'] is False


@pytest.mark.parametrize('field,value', [
    ('python', '3.11.14'), ('platform', 'linux'), ('machine', 'ARM64'),
    ('isolated', False), ('pythonpath', 'foreign/imports'),
    ('packages', {'fixture': '1.2.4'}), ('packages', {}),
    ('packages', {'fixture': '1.2.3', 'unlocked': '1'}),
    ('freecad', None), ('freecad', {'version':'1.1.1', 'python':'3.11.14', 'occ':'7.9'}),
    ('sdk_packages', {}), ('sdk_packages', {'fixture-compiler': '9.0'}),
    ('contrib_packages', {}), ('contrib_packages', {'contrib': '2.1'}),
    ('contrib_packages', {'contrib': '2.0', 'unlocked': '1'})])
def test_incompatible_or_inherited_environment_refuses(environment, field, value):
    from engineering import toolchain as T
    root, policy, observed = environment
    observed[field] = value
    assert T.diagnose(root, policy, observed)['status'] == 'BLOCKED'


def test_lock_and_source_eol_are_real_inputs(environment):
    from engineering import toolchain as T
    root, policy, observed = environment
    (root / 'source.py').write_bytes(b'print("fixture")\r\n')
    assert T.diagnose(root, policy, observed)['status'] == 'BLOCKED'
    (root / 'source.py').write_bytes(b'print("fixture")\n')
    (root / 'requirements.lock').write_bytes(b'changed')
    assert T.diagnose(root, policy, observed)['status'] == 'BLOCKED'


@pytest.mark.parametrize('text', ['fixture>=1', 'fixture==1.0', '-e ../local',
    'fixture @ https://example.invalid/source.tar.gz',
    'fixture==1 --hash=sha256:bad', 'fixture==1 --hash=sha256:' + 'a'*64 + '\nfixture==2 --hash=sha256:' + 'b'*64])
def test_unpinned_unhashed_or_ambiguous_lock_refuses(text):
    from engineering import toolchain as T
    with pytest.raises(ValueError):
        T.parse_lock(text)


def test_lock_accepts_generated_continuations_and_comments():
    from engineering import toolchain as T
    assert T.parse_lock('# generated\nFixture_Name==1.0 \\\n    --hash=sha256:' + 'a'*64 + '\n    # via parent\n') == {'fixture-name': '1.0'}


def test_bootstrap_does_not_adopt_an_existing_directory(tmp_path):
    from engineering import toolchain as T
    path = tmp_path / 'existing'
    path.mkdir()
    (path / 'owner.txt').write_text('keep')
    with pytest.raises(ValueError, match='EXISTS'):
        T.new_prefix(path)
    assert (path / 'owner.txt').read_text() == 'keep'


def test_bootstrap_rejects_wrong_python_before_creating_environment(environment):
    import sys
    from engineering import toolchain as T
    root, policy, _ = environment
    policy['python'] = '0.0.0'
    destination = root / 'new-environment'
    with pytest.raises(ValueError, match='PYTHON_VERSION'):
        T.bootstrap(root, policy, destination, python=sys.executable)
    assert not destination.exists()


def test_supported_sdk_path_budget_is_checked_before_compilation(environment):
    from engineering import toolchain as T
    root, policy, observed = environment
    policy['max_sdk_root_chars'] = 32
    observed['sdk_root'] = 'C:/short-sdk'
    assert T.diagnose(root,policy,observed)['status'] == 'READY'
    observed['sdk_root'] = 'C:/' + 'long-path-'*10
    assert any('SDK_PATH' in p for p in T.diagnose(root,policy,observed)['problems'])
