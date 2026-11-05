"""Un proceso por LASER_ID. Sheets es la cola; el bloqueo SMB evita duplicados."""
import argparse
import json
import logging
import os
import re
import time
from pathlib import Path
from .cpa_browser import CPABrowser, CPAError, profile_order
from .kyoodo import actionable_rows, read_remote_queue, PENDING, RETRY, DONE, ALREADY, HIGHER

TERMINAL = (DONE, ALREADY, HIGHER)
IDENTITY = ('ID_REGISTRO', '# DE ORDEN', 'LINEA', 'ID_CPA', 'LASER_ID', 'STATUS_DESTINO', 'SECUENCIA')


class LaserLock:
    """Bloqueo del SO mantenido toda la ejecución; también funciona sobre SMB.

    El archivo no se borra: evita carreras sobre inodos y se libera al morir
    el proceso. Todos los equipos deben usar el mismo directorio compartido.
    """
    def __init__(self, directory, laser):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,50}', laser):
            raise ValueError('LASER_ID inválido.')
        self.path = Path(directory) / (laser.lower() + '.lock')
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a+b')
        if self.file.seek(0, 2) == 0:
            self.file.write(b'0'); self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise ValueError('No se pudo bloquear el láser: otra instancia está activa o el recurso compartido no permite bloqueos.') from None
        return self

    def __exit__(self, *args):
        self.file.close()


class SheetQueue:
    def __init__(self, book, laser):
        self.book, self.laser = book, laser
        self.ws = book.worksheet('Kyoodo')

    def jobs(self):
        return [j for j in actionable_rows(self.ws.get_all_values()) if j['LASER_ID'] == self.laser]

    def current(self, job):
        remote, header = read_remote_queue(self.ws)
        if not header or job['ID_REGISTRO'] not in remote:
            raise CPAError('El trabajo desapareció o cambió la cabecera; operación detenida.')
        entry = remote[job['ID_REGISTRO']]
        from .kyoodo import QUEUE_HEADERS
        item = dict(zip(QUEUE_HEADERS, entry['values']))
        if any(str(item[k]) != str(job[k]) for k in IDENTITY) or item['LASER_ID'] != self.laser:
            raise CPAError('Cambió la identidad del trabajo; operación detenida.')
        return entry, item

    def guard(self, job):
        _, current = self.current(job)
        if current['ESTADO'] not in (PENDING, RETRY):
            raise CPAError('El trabajo ya no está pendiente.')

    def result(self, job, state, comment):
        entry, current = self.current(job)
        if current['ESTADO'] in TERMINAL:
            return
        if current['ESTADO'] not in (PENDING, RETRY):
            raise CPAError('El trabajo ya no está habilitado.')
        row = entry['row']
        self.book.values_batch_update({'valueInputOption': 'RAW', 'data': [
            {'range': f"'Kyoodo'!D{row}", 'values': [[comment]]},
            {'range': f"'Kyoodo'!H{row}", 'values': [[state]]},
        ]})

    def credentials(self):
        rows = self.book.worksheet('Cuentas CPA').get_all_values()
        if not rows or rows[0][:4] != ['LASER_ID', 'LASER', 'CORREO', 'CONTRASEÑA']:
            raise CPAError('Cuentas CPA requiere LASER_ID, LASER, CORREO, CONTRASEÑA.')
        matches = [r for r in rows[1:] if r and r[0] == self.laser]
        if len(matches) != 1 or len(matches[0]) < 4 or not matches[0][2].strip() or not matches[0][3]:
            raise CPAError('Falta una cuenta CPA única y completa para este LASER_ID.')
        # Contraseña se conserva exactamente; nunca se imprime ni se registra.
        return matches[0][2].strip(), matches[0][3]


def run_cycle(queue, browser_factory, log=logging.info):
    jobs = queue.jobs()
    for job in jobs:
        browser = None
        try:
            queue.guard(job)
            profile_order(job['# DE ORDEN'])
            if str(job['STATUS_DESTINO']) != '910':
                raise CPAError('Destino distinto de 910; no se modificó CPA.')
            email, password = queue.credentials()
            browser = browser_factory()
            browser.login(email, password)
            result, comment = browser.update(job, before_save=lambda: queue.guard(job))
            queue.result(job, result, comment)
            log('%s | %s | %s', job['# DE ORDEN'], job['LINEA'], result)
        except Exception as exc:
            # Los errores Selenium/HTTP pueden incluir HTML o credenciales.
            comment = str(exc) if isinstance(exc, CPAError) else 'Fallo de conexión, sesión o guardado. Se revisará el estado en CPA antes de reintentar.'
            try:
                queue.result(job, RETRY, comment)
            except Exception:
                log('No se pudo registrar el resultado. Se releerá la cola antes de reintentar.')
            log('%s | %s | REINTENTAR: %s', job['# DE ORDEN'], job['LINEA'], comment)
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass


def main():
    parser = argparse.ArgumentParser(description='Kyoodo Corte: una computadora por láser')
    parser.add_argument('--config', default='kyoodo.json')
    parser.add_argument('--once', action='store_true', help='Un solo ciclo, sí actualiza CPA')
    parser.add_argument('--check', action='store_true', help='Comprobar cola y cuenta, sin abrir ni modificar CPA')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    path = Path(args.config).resolve()
    if not path.is_file():
        raise ValueError('Copia kyoodo.example.json como kyoodo.json y configura este láser.')
    config = json.loads(path.read_text(encoding='utf-8'))
    laser = config['laser_id']
    # En producción exigir UNC asegura que el bloqueo no quede solo en una PC.
    lock_dir = config['lock_directory']
    if os.name == 'nt' and not lock_dir.startswith('\\\\'):
        raise ValueError('lock_directory debe ser una ruta UNC compartida idéntica en todos los equipos.')
    interval = max(30, int(config.get('poll_seconds', 60)))
    with LaserLock(lock_dir, laser):
        import gspread
        credentials = path.parent / config['credentials']
        book = gspread.service_account(filename=str(credentials)).open_by_key(config['spreadsheet_id'])
        queue = SheetQueue(book, laser)
        if args.check:
            queue.credentials()
            logging.info('Láser %s: %s pendientes; cuenta configurada. CPA no fue modificado.', laser, len(queue.jobs()))
            return
        logging.info('Kyoodo activo para %s. Ctrl+C para detener.', laser)
        while True:
            try:
                run_cycle(queue, lambda: CPABrowser(config))
            except Exception:
                logging.error('No se pudo leer la cola. Revisa conexión, permisos y estructura del Sheet.')
            if args.once:
                return
            time.sleep(interval)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
