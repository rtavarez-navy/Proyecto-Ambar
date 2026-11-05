"""Servicio central de planta. No ejecutar uno por operador."""
import argparse
import getpass
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .core import Store
from .sheets import SheetsSync

MAX_BODY = 64 * 1024 * 1024


class App:
    def __init__(self, store, sync=None):
        self.store, self.sync = store, sync
        self.sessions = {}
        self.attempts = {}
        self.lock = threading.RLock()

    def call(self, path, data, token='', ip='local'):
        with self.lock:
            now = time.monotonic()
            self.sessions = {k: v for k, v in self.sessions.items() if v[1] > now}
            self.attempts = {k: [t for t in v if now - t < 300] for k, v in self.attempts.items() if v and now - v[-1] < 300}
            if path == '/login':
                attempts = self.attempts.setdefault(ip, [])
                if len(attempts) >= 10:
                    raise ValueError('Demasiados intentos. Espera cinco minutos.')
                attempts.append(now)
                user = self.store.login(data.get('code', ''), data.get('password', ''))
                attempts.clear()
                token = secrets.token_urlsafe(32)
                self.sessions[token] = (user['code'], now + 8 * 3600)
                return {'token': token, 'user': user}
            session = self.sessions.get(token)
            if not session:
                raise PermissionError('Sesión vencida. Inicia sesión otra vez.')
            actor = session[0]
            if path == '/logout':
                self.sessions.pop(token, None)
                return {'ok': True}
            if path == '/password':
                self.store.change_password(actor, data.get('old', ''), data.get('new', ''))
                self.sessions = {k: v for k, v in self.sessions.items() if v[0] != actor or k == token}
                return {'ok': True}
            if self.store.user(actor)['must_change']:
                raise ValueError('Primero debes cambiar tu clave.')
            if path == '/snapshot':
                return self.store.snapshot(actor)
            if path == '/employee':
                self.store.add_employee(actor, data.get('code'), data.get('name'))
            elif path == '/laser':
                self.store.save_laser(actor, data.get('name'), data.get('account', ''), data.get('laser_id'))
            elif path == '/assign':
                self.store.assign(actor, data.get('code'), data.get('laser_id'))
            elif path == '/cpa':
                return {'count': self.store.import_cpa(actor, data.get('rows', []))}
            elif path == '/report':
                return {'id': self.store.report(actor, data.get('request_id'), data.get('order'), data.get('line'), data.get('qty'), data.get('comment', ''), data.get('reprint', False), data.get('laser_id'))}
            elif path == '/sync':
                self.store.supervisor(actor)
                if not self.sync:
                    raise ValueError('Google Sheets todavía no está configurado en el servidor.')
                threading.Thread(target=self.sync.sync, daemon=True).start()
            else:
                raise ValueError('Operación desconocida.')
            return {'ok': True}


def handler(app):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if size < 1 or size > MAX_BODY:
                    raise ValueError('Solicitud vacía o demasiado grande (máximo 64 MB).')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('Solicitud inválida.')
                token = self.headers.get('Authorization', '').removeprefix('Bearer ')
                result = app.call(self.path, data, token, self.client_address[0])
                self.respond(200, result)
            except PermissionError as exc:
                self.respond(401, {'error': str(exc)})
            except (ValueError, TypeError, KeyError, sqlite3.IntegrityError) as exc:
                self.respond(400, {'error': 'Ya existe ese código o nombre, o la referencia no es válida.' if isinstance(exc, sqlite3.IntegrityError) else str(exc)})
            except Exception:
                logging.exception('Error del servicio')
                self.respond(500, {'error': 'Error interno. Revisa la consola del servidor.'})

        def respond(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            logging.info('%s %s', self.client_address[0], fmt % args)
    return Handler


def main():
    parser = argparse.ArgumentParser(description='Servidor central Corte SK2 2027')
    parser.add_argument('--config', default='server.json')
    parser.add_argument('--restore-sheet', action='store_true')
    args = parser.parse_args()
    config_path = Path(args.config).resolve()
    if not config_path.exists():
        print('Primera configuración del servicio central.')
        config = {'host': '0.0.0.0', 'port': 8765, 'database': 'datos/corte.db', 'spreadsheet_id': '', 'credentials': 'google-service-account.json'}
        config_path.write_text(json.dumps(config, indent=2), encoding='utf-8')
    config = json.loads(config_path.read_text(encoding='utf-8'))
    def local_path(value):
        return config_path.parent / value
    database = local_path(config['database'])
    database.parent.mkdir(parents=True, exist_ok=True)
    store = Store(database)
    sync = SheetsSync(store, config['spreadsheet_id'], str(local_path(config['credentials']))) if config.get('spreadsheet_id') else None
    if args.restore_sheet:
        if not sync:
            raise ValueError('Configura Google Sheets antes de recuperar.')
        sync.restore_empty()
    if not store.db.execute('SELECT 1 FROM users').fetchone():
        code = input('Código del supervisor inicial: ').strip()
        password = getpass.getpass('Clave inicial del supervisor (mínimo 8 caracteres): ')
        if password != getpass.getpass('Repite la clave: '):
            raise ValueError('Las claves no coinciden.')
        store.bootstrap(code, password)
    if sync:
        threading.Thread(target=sync.run, daemon=True).start()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    server = ThreadingHTTPServer((config.get('host', '0.0.0.0'), config.get('port', 8765)), handler(app=App(store, sync)))
    print(f'Corte SK2 · puerto {server.server_port} · Google Sheets: {"configurado" if sync else "PENDIENTE DE CONFIGURAR"}')
    print('Mantén esta ventana abierta. Usa una sola instancia del servidor para todas las láseres.')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
