"""Set/rotate the visitor dashboard's owner passcode without storing plaintext."""
import getpass
import hashlib
import json
import os
import tempfile
from pathlib import Path


def main():
    path = Path('data/cloud/public-shutdown.json')
    if not path.parent.is_dir():
        raise SystemExit('Prepare the cloud deployment first; data/cloud is missing.')
    passcode = getpass.getpass('New owner passcode (16–256 characters): ')
    if not 16 <= len(passcode) <= 256:
        raise SystemExit('Use 16–256 characters, ideally a password-manager-generated passcode.')
    if passcode != getpass.getpass('Confirm passcode: '):
        raise SystemExit('Passcodes do not match.')
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac('sha256', passcode.encode(), salt, 600_000)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.shutdown-')
    try:
        with os.fdopen(descriptor, 'w') as output:
            json.dump(dict(salt=salt.hex(), digest=digest.hex()), output)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print('Passcode hash saved. Restart project15-public-dashboard to apply it.')


if __name__ == '__main__':
    main()
