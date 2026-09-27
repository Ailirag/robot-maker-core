"""Bootstrap, install a fresh SDK, or diagnose the declared runtime. No upload."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys

from engineering import toolchain as T, runtime_probe as R, runtime_smoke as S
from engineering.build_composition import file_pin, _path


def install_sdk(root, policy, directory, *, executable, project_dir, environment):
    root, directory = Path(root).resolve(), Path(directory).resolve()
    if len(str(directory)) > policy.get('max_sdk_root_chars', 32767):
        raise ValueError('SDK_PATH: choose a shorter fresh directory before installation')
    if directory.exists():
        raise ValueError('EXISTS: an existing SDK is only inspected, never adopted by the installer')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', environment):
        raise ValueError('SDK_ENVIRONMENT: invalid environment name')
    project = _path(root, project_dir)
    config = file_pin(root, project_dir+'/platformio.ini', 'canonical_lf')
    packages = T.locked_packages(root, policy['requirements'])
    contrib = T.locked_packages(root, policy['contrib_requirements'])
    env = T.clean_environment()
    version = subprocess.run([str(executable),'--version'],env=env,capture_output=True,text=True,encoding='utf-8',check=True)
    if version.stdout.strip() != 'PlatformIO Core, version '+packages['platformio']:
        raise ValueError('SDK_TOOL: PlatformIO version differs from the lock')
    directory = T.new_prefix(directory)
    constraints = directory/'SDK_CONSTRAINTS.txt'
    constraints.write_text(''.join(f'{k}=={v}\n' for k,v in sorted(contrib.items())),encoding='utf-8',newline='\n')
    env.update(PLATFORMIO_CORE_DIR=str(directory),PIP_CONSTRAINT=str(constraints))
    command = [str(executable),'pkg','install','--project-dir',str(project),'-e',environment]
    with (directory/'SDK_INSTALL.log').open('x',encoding='utf-8',newline='\n') as log:
        result = subprocess.run(command,cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT)
    if result.returncode:
        raise ValueError('SDK_INSTALL_FAILED: inspect the retained SDK_INSTALL.log')
    if config != file_pin(root, project_dir+'/platformio.ini','canonical_lf'):
        raise ValueError('SDK_INPUT_DRIFT: project configuration changed')
    actual, nested = R.sdk(directory), R.packages(directory/'packages/tool-esptoolpy/_contrib')
    if actual != policy['sdk_packages'] or nested != contrib:
        raise ValueError('SDK_VERSION_DRIFT: installed package closure differs from the contract')
    record = {'schema':'engineering-sdk-install-v1','recorded_at':datetime.now(timezone.utc).isoformat(),
              'toolchain_id':policy['id'],'platformio':version.stdout.strip(),'project_config':config,
              'contrib_requirements':policy['contrib_requirements'],'sdk_packages':actual,
              'contrib_packages':nested,'physical_approval':False}
    S.write(directory/'SDK_INSTALL.json',record)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path.cwd())
    parser.add_argument('--policy',default='engineering/runtime/toolchain.json')
    commands = parser.add_subparsers(dest='action',required=True)
    boot = commands.add_parser('bootstrap')
    boot.add_argument('--prefix',type=Path,required=True)
    boot.add_argument('--python',default=sys.executable)
    sdk = commands.add_parser('sdk')
    sdk.add_argument('--directory',type=Path,required=True)
    sdk.add_argument('--platformio',required=True)
    sdk.add_argument('--project-dir',required=True)
    sdk.add_argument('--environment',required=True)
    doctor = commands.add_parser('doctor')
    doctor.add_argument('--sdk-root',type=Path,required=True)
    doctor.add_argument('--freecadcmd',required=True)
    doctor.add_argument('--report',type=Path)
    smoke = commands.add_parser('smoke')
    smoke.add_argument('--destination',type=Path,required=True)
    smoke.add_argument('--freecadcmd',required=True)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        policy = json.loads(_path(root,args.policy).read_text(encoding='utf-8'))
        if args.action == 'bootstrap':
            report = T.bootstrap(root,policy,args.prefix,python=args.python)
        elif args.action == 'sdk':
            report = install_sdk(root,policy,args.directory,executable=args.platformio,
                                 project_dir=args.project_dir,environment=args.environment)
        elif args.action == 'smoke':
            S.export(args.destination)
            report = S.exchange(args.destination,args.freecadcmd)
        else:
            observation = None
            try:
                observation = R.observe(args.sdk_root,args.freecadcmd)
                result = T.diagnose(root,policy,observation)
            except (ValueError,OSError) as exc:
                result = {'status':'BLOCKED','problems':[str(exc)],'physical_approval':False}
            report = {'schema':'engineering-doctor-v1','policy':file_pin(root,args.policy),
                      'recorded_at':datetime.now(timezone.utc).isoformat(),
                      'observation':observation,'result':result,'physical_approval':False}
            if args.report:
                args.report.parent.mkdir(parents=True,exist_ok=True)
                S.write(args.report,report)
            print(json.dumps(report,ensure_ascii=False,indent=2))
            return 0 if result['status']=='READY' else 4
        print(json.dumps(report,ensure_ascii=False,indent=2))
        return 0
    except (ValueError,OSError,KeyError,subprocess.CalledProcessError) as exc:
        print(json.dumps({'status':'BLOCKED','problems':[str(exc)],'physical_approval':False},ensure_ascii=False))
        return 4


if __name__ == '__main__':
    raise SystemExit(main())
