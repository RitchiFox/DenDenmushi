#!/usr/bin/env python3
"""Explicit source update. Never installs drivers, contacts Pi or runs audio."""
import argparse
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

REPOSITORY = 'https://github.com/TimeSkipe/DenDenmushi.git'
BUNDLE_ID = 'dev.denden.demo.local'


def validate_target(target):
    if target.suffix != '.app' or target.is_symlink():
        raise ValueError('Expected a real application directory, not a symlink')
    with (target / 'Contents/Info.plist').open('rb') as stream:
        if plistlib.load(stream).get('CFBundleIdentifier') != BUNDLE_ID:
            raise ValueError('Unexpected application identity')


def replace_app(target, staged, backup):
    """All paths share a filesystem; restore the original on swap failure."""
    if backup.exists():
        raise ValueError('Backup path already exists')
    target.rename(backup)
    try:
        staged.rename(target)
    except BaseException:
        backup.rename(target)
        raise


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', required=True, type=Path)
    parser.add_argument('--pid', required=True, type=int)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    if sys.platform != 'darwin' or not re.fullmatch('[0-9a-f]{40}', args.commit) or args.pid <= 1:
        parser.error('Requires macOS, a parent process ID and a complete Git commit hash')
    target = args.target.absolute()
    validate_target(target)
    backup = target.with_name(target.stem + '-backup-' + uuid.uuid4().hex[:10] + '.app')
    environment = dict(os.environ)
    # Never inherit an unrelated checkout/build destination or signing identity.
    for key in list(environment):
        if key.startswith(('GIT_', 'DENDEN_BUILD_', 'DENDEN_SIGN_')):
            environment.pop(key)
    environment['GIT_TERMINAL_PROMPT'] = '0'
    try:
        print('Waiting for DenDenMushi to close. No Pi services will be changed.', flush=True)
        for _ in range(120):
            try:
                os.kill(args.pid, 0)
            except ProcessLookupError:
                break
            time.sleep(1)
        else:
            raise RuntimeError('Application did not exit; update cancelled')
        # Stage alongside the app so the final swap is a same-volume rename.
        with tempfile.TemporaryDirectory(prefix='.denden-update-', dir=target.parent) as folder:
            work = Path(folder)
            source = work / 'source'
            run('/usr/bin/git', 'clone', '--no-checkout', REPOSITORY, str(source), env=environment)
            run('/usr/bin/git', '-C', str(source), 'checkout', '--detach', args.commit, env=environment)
            actual = subprocess.check_output(['/usr/bin/git', '-C', str(source), 'rev-parse', 'HEAD'], env=environment, text=True).strip()
            if actual != args.commit:
                raise RuntimeError('Downloaded commit does not match the selected update')
            run('/usr/bin/python3', 'scripts/build-wifi-drivers.py', cwd=source, env=environment)
            run('/bin/bash', 'scripts/build-app.sh', cwd=source, env=environment)
            staged = source / 'build/DenDenMushi.app'
            validate_target(staged)
            run('/usr/bin/codesign', '--verify', '--strict', str(staged))
            replace_app(target, staged, backup)
        print('Update installed. Previous app: ' + str(backup), flush=True)
        run('/usr/bin/open', str(target))
        return 0
    except Exception as error:
        print('UPDATE FAILED: ' + str(error), file=sys.stderr)
        print('Your app/settings were not deleted. If replacement completed, the backup is: ' + str(backup), file=sys.stderr)
        # Reopen the preserved app after a build/network failure, but do not
        # launch a second copy if the original process never exited.
        try:
            os.kill(args.pid, 0)
        except ProcessLookupError:
            if target.exists():
                subprocess.run(['/usr/bin/open', str(target)], check=False)
        return 1


if __name__ == '__main__':
    sys.exit(main())
