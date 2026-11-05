"""Servicio Python alojado en Google Cloud Run; Sheets es el almacenamiento central."""
import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from .cloud_store import SheetsDatabase


class BusyError(Exception):
    pass


class StorageMutex:
    """Un solo escritor incluso durante cambios de revisión. No expira por tiempo."""
    def __init__(self, bucket, spreadsheet_id):
        self.bucket, self.name = bucket, 'locks/corte-' + spreadsheet_id + '.json'

    @contextmanager
    def acquire(self):
        from google.api_core.exceptions import PreconditionFailed
        blob = self.bucket.blob(self.name)
        try:
            blob.upload_from_string(json.dumps({'created': time.time()}), content_type='application/json', if_generation_match=0, retry=None, timeout=15)
        except PreconditionFailed:
            raise BusyError('Otra terminal está guardando. Reintenta en unos segundos.') from None
        generation = int(blob.generation)
        try:
            yield
        finally:
            blob.delete(if_generation_match=generation, retry=None, timeout=15)


class CloudService:
    def __init__(self, database, mutex, supervisor_code='', supervisor_hash=''):
        self.db, self.mutex = database, mutex
        self.supervisor_code, self.supervisor_hash = supervisor_code, supervisor_hash
        self.ready = False
        self.thread_lock = threading.RLock()

    def call(self, path, data, token=''):
        with self.thread_lock:
            if not self.ready:
                with self.mutex.acquire():
                    self.db.initialize(self.supervisor_code, self.supervisor_hash)
                self.ready = True
            if path == '/snapshot':
                self.db.load()
                return self.db.call(path, data, token)
            with self.mutex.acquire():
                self.db.load()
                try:
                    result = self.db.call(path, data, token)
                except ValueError:
                    if path == '/login':
                        self.db.commit()  # Persistir intentos fallidos, nunca otras mutaciones rechazadas.
                    raise
                self.db.commit()
                return result


def create_app(service=None):
    from flask import Flask, jsonify, request
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024
    holder, guard = [service], threading.Lock()

    def backend():
        with guard:
            if holder[0] is None:
                import google.auth
                import gspread
                from google.cloud import storage
                sid = os.environ['CORTE_SPREADSHEET_ID']
                creds, project = google.auth.default(scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/devstorage.read_write'])
                book = gspread.authorize(creds).open_by_key(sid)
                bucket = storage.Client(project=project).bucket(os.environ['CORTE_LOCK_BUCKET'])
                holder[0] = CloudService(SheetsDatabase(book), StorageMutex(bucket, sid), os.environ.get('CORTE_SUPERVISOR_CODE', ''), os.environ.get('CORTE_SUPERVISOR_HASH', ''))
            return holder[0]

    @app.get('/health')
    def health():
        return jsonify({'ok': True, 'backend': 'google-sheets'})

    @app.post('/<path:operation>')
    def dispatch(operation):
        try:
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                return jsonify({'error': 'Solicitud JSON inválida.'}), 400
            token = request.headers.get('Authorization', '').removeprefix('Bearer ')
            result = backend().call('/' + operation, data, token)
            response = jsonify(result)
            response.headers['Cache-Control'] = 'no-store'
            return response
        except BusyError as exc:
            return jsonify({'error': str(exc)}), 503, {'Retry-After': '3'}
        except PermissionError as exc:
            return jsonify({'error': str(exc)}), 403
        except (ValueError, TypeError) as exc:
            return jsonify({'error': str(exc)}), 400
        except Exception:
            # Nunca registrar cuerpo, tokens ni credenciales de las solicitudes.
            logging.error('Fallo al acceder a Google Sheets; revisar permisos, cuotas y conectividad.')
            return jsonify({'error': 'No se pudo confirmar la operación en Google Sheets. Reintenta con el mismo envío.'}), 503

    return app
