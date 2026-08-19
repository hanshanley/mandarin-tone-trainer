#!/usr/bin/env python3
"""Create or validate the private Android release-signing configuration."""
import argparse
import getpass
import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KEYSTORE = Path.home() / '.local' / 'share' / 'mandarin-tone-trainer' / 'release.jks'


def property_value(value):
    return (
        str(value)
        .replace('\\', '\\\\')
        .replace('\n', '\\n')
        .replace('\r', '\\r')
        .replace('\t', '\\t')
    )


def write_signing_properties(path, keystore, alias, store_password, key_password):
    path.write_text(
        '\n'.join([
            f'storeFile={property_value(keystore.resolve())}',
            f'storePassword={property_value(store_password)}',
            f'keyAlias={property_value(alias)}',
            f'keyPassword={property_value(key_password)}',
            '',
        ]),
        encoding='utf-8',
    )
    path.chmod(0o600)


def confirmed_password(prompt):
    password = getpass.getpass(prompt)
    if len(password) < 6:
        raise SystemExit('The keystore password must contain at least 6 characters.')
    if password != getpass.getpass('Confirm password: '):
        raise SystemExit('The passwords did not match. No signing configuration was written.')
    return password


def keytool_path():
    local = Path.home() / '.local' / 'bin' / 'keytool'
    if local.is_file():
        return str(local)
    command = shutil.which('keytool')
    if command:
        return command
    raise SystemExit(
        'keytool is unavailable. Run python3 scripts/bootstrap.py first.'
    )


def create_keystore(command, path, alias, password):
    path.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment['MTT_KEYSTORE_PASSWORD'] = password
    subprocess.run(
        [
            command,
            '-genkeypair',
            '-keystore',
            str(path),
            '-alias',
            alias,
            '-keyalg',
            'RSA',
            '-keysize',
            '4096',
            '-validity',
            '10000',
            '-dname',
            'CN=Mandarin Tone Trainer',
            '-storepass:env',
            'MTT_KEYSTORE_PASSWORD',
            '-keypass:env',
            'MTT_KEYSTORE_PASSWORD',
            '-noprompt',
        ],
        check=True,
        env=environment,
    )
    path.chmod(0o600)


def validate_keystore(command, path, alias, password):
    environment = os.environ.copy()
    environment['MTT_KEYSTORE_PASSWORD'] = password
    result = subprocess.run(
        [
            command,
            '-list',
            '-keystore',
            str(path),
            '-alias',
            alias,
            '-storepass:env',
            'MTT_KEYSTORE_PASSWORD',
        ],
        capture_output=True,
        text=True,
        env=environment,
    )
    if result.returncode:
        raise SystemExit(
            'The keystore password or alias is incorrect. '
            'No signing configuration was written.'
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--keystore', type=Path, default=DEFAULT_KEYSTORE)
    parser.add_argument('--alias', default='mandarin-tone-trainer')
    args = parser.parse_args()

    command = keytool_path()
    if args.keystore.exists():
        password = getpass.getpass('Existing keystore password: ')
        validate_keystore(command, args.keystore, args.alias, password)
    else:
        print(f'Creating private release keystore at {args.keystore}')
        password = confirmed_password('New keystore password: ')
        create_keystore(command, args.keystore, args.alias, password)

    properties = ROOT / 'keystore.properties'
    write_signing_properties(
        properties,
        args.keystore,
        args.alias,
        password,
        password,
    )
    print(f'Release signing is configured in {properties}.')
    print('The keystore and properties file are private and ignored by Git.')
    print('Build the signed APK with: npm run android:release')


if __name__ == '__main__':
    main()
