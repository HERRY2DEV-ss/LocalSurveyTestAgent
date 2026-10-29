from getpass import getpass
from pathlib import Path

from dotenv import dotenv_values
from werkzeug.security import generate_password_hash

BASE = Path(__file__).resolve().parent
ENV_FILE = BASE / '.env'


def main():
    password = getpass('Choose a local dashboard password (at least 12 characters): ')
    confirmation = getpass('Confirm password: ')
    if len(password) < 12:
        raise SystemExit('Password must be at least 12 characters.')
    if password != confirmation:
        raise SystemExit('Passwords do not match.')

    values = dotenv_values(ENV_FILE) if ENV_FILE.exists() else {}
    values['LOCAL_TEST_PASSWORD_HASH'] = generate_password_hash(password)
    if not values.get('APP_SECRET_KEY'):
        values.pop('APP_SECRET_KEY', None)
    lines = [f'{key}={value}' for key, value in values.items() if value is not None]
    ENV_FILE.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'Password hash saved to {ENV_FILE.name}; the password itself was not saved.')


if __name__ == '__main__':
    main()
