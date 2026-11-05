"""Genera configuración privada en Cloud Shell; no solicita claves de Google."""
import getpass
import json
import os
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from corte_sk2.core import password_hash


def main():
    raw = input('Enlace o ID del Sheet de corte: ').strip()
    sid = raw.split('/spreadsheets/d/', 1)[-1].split('/', 1)[0].split('?', 1)[0]
    if not re.fullmatch(r'[A-Za-z0-9_-]{20,}', sid):
        raise ValueError('ID del Sheet inválido.')
    bucket = input('Nombre del bucket privado para coordinación: ').strip()
    code = input('Código del supervisor inicial (se conserva el existente si hay migración): ').strip()
    password = getpass.getpass('Clave inicial del supervisor, mínimo 8 caracteres: ')
    if not code or len(password) < 8 or password == '123456':
        raise ValueError('Código o clave inicial inválidos.')
    if password != getpass.getpass('Repite la clave: '):
        raise ValueError('Las claves no coinciden.')
    target = Path('cloud.env.yaml')
    target.write_text(json.dumps({'CORTE_SPREADSHEET_ID': sid, 'CORTE_LOCK_BUCKET': bucket,
                                  'CORTE_SUPERVISOR_CODE': code, 'CORTE_SUPERVISOR_HASH': password_hash(password)}, indent=2), encoding='utf-8')
    os.chmod(target, 0o600)
    print('Configuración privada guardada. No la subas a GitHub.')


if __name__ == '__main__':
    main()
