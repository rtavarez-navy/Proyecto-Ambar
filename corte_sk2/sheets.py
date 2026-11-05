"""Directorio de entrada y proyección recuperable al Sheet de Corte SK2."""
import json
import re
import threading
import time
from .core import identifier, stamp, text
from .kyoodo import read_remote_queue, needs_queue_push, push_queue, QUEUE_HEADERS

LEGACY_TABLES = ('lasers', 'users', 'cpa', 'reports', 'audit')
TABLES = LEGACY_TABLES + ('kyoodo_jobs',)
SPREADSHEET_ID = ''
HEADERS = ['FECHA', '# DE ORDEN', 'LINEA', '# PIEZAS', 'COMENTARIOS', 'LASER_ID', 'CODIGO_EMPLEADO', 'NOMBRE_OPERADOR', 'HORA_REGISTRO', 'ID_REGISTRO', 'VALIDACION', 'ALERTA', 'REPRINT']
CONFIG_HEADERS = ['LASER_ID', 'NOMBRE', 'HOJA', 'ORDEN', 'ACTIVO']
RESERVED = {'Maestro', 'Configuración', 'Consolidado', 'Kyoodo', 'Asignaciones', 'Cuentas CPA', 'CPA DATA', '_Estado'}


def tab_name(laser):
    return laser.get('sheet_title') or f"Láser {laser['id']:02d}"


def parse_directory(master, config):
    if not master or not {'APELLIDOS_NOMBRES', 'CODIGO_EMPLEADO'} <= set(master[0]):
        raise ValueError('Maestro requiere APELLIDOS_NOMBRES y CODIGO_EMPLEADO.')
    name_col, code_col = master[0].index('APELLIDOS_NOMBRES'), master[0].index('CODIGO_EMPLEADO')
    employees = {}
    for index, row in enumerate(master[1:], 2):
        if not any(text(v) for v in row):
            continue
        row = list(row) + [''] * len(master[0])
        code, name = identifier(row[code_col]), text(row[name_col])
        if not code or not name:
            raise ValueError(f'Maestro fila {index}: código y nombre requeridos.')
        if code in employees and employees[code] != name:
            raise ValueError(f'Maestro: código duplicado con nombres diferentes ({code}).')
        employees[code] = name
    if not config or config[0][:5] != CONFIG_HEADERS:
        raise ValueError('Configuración requiere LASER_ID, NOMBRE, HOJA, ORDEN, ACTIVO.')
    lasers, keys, titles, names = [], set(), set(), set()
    for index, row in enumerate(config[1:], 2):
        row = list(row) + [''] * 5
        key, name, title, order, active = row[:5]
        if not any(text(v) for v in (key, name, title, order)):
            continue  # Filas vacías con checkbox FALSE no son láseres.
        key, name, title = text(key), text(name), text(title)
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,50}', key):
            raise ValueError(f'Configuración fila {index}: LASER_ID inválido.')
        if not name or len(name) > 60 or not title or len(title) > 100 or re.search(r'[\[\]:*?/\\]', title) or title in RESERVED:
            raise ValueError(f'Configuración fila {index}: nombre u hoja no válidos.')
        if key in keys or title in titles or name in names:
            raise ValueError('Configuración contiene id, nombre u hoja duplicados.')
        truth = text(active).lower()
        if truth not in ('true', 'false', '1', '0', 'verdadero', 'falso'):
            raise ValueError(f'Configuración fila {index}: ACTIVO debe ser una casilla TRUE/FALSE.')
        try:
            position = int(order)
        except (ValueError, TypeError):
            raise ValueError(f'Configuración fila {index}: ORDEN debe ser entero.') from None
        lasers.append({'external_id': key, 'name': name, 'sheet_title': title, 'sort_order': position, 'active': int(truth in ('true', '1', 'verdadero'))})
        keys.add(key); titles.add(title); names.add(name)
    if not lasers:
        raise ValueError('Configuración no tiene láseres. No se alteró el catálogo local.')
    return list(employees.items()), lasers


class SheetsSync:
    def __init__(self, store, spreadsheet_id, credentials):
        self.store, self.spreadsheet_id, self.credentials = store, spreadsheet_id, credentials
        self.guard = threading.Lock()

    def book(self):
        from pathlib import Path
        if not Path(self.credentials).is_file():
            raise ValueError('Falta google-service-account.json. Ejecuta el servidor con --connect-google.')
        import gspread
        return gspread.service_account(filename=self.credentials).open_by_key(self.spreadsheet_id)

    def sync(self):
        if not self.guard.acquire(blocking=False):
            return
        try:
            self._sync()
        except Exception as exc:
            if isinstance(exc, ValueError):
                detail = str(exc)
            else:
                detail = f'{type(exc).__name__}: no se pudo sincronizar. Comprueba credenciales, permisos del Sheet y conexión.'
            with self.store.lock, self.store.db:
                self.store.db.execute("INSERT INTO meta VALUES('sync_error',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (detail,))
        finally:
            self.guard.release()

    def _sync(self):
        s, book = self.store, self.book()
        worksheets = {w.title: w for w in book.worksheets()}
        if 'Maestro' not in worksheets or 'Configuración' not in worksheets:
            raise ValueError('No se encontraron Maestro y Configuración en el Sheet.')
        master = worksheets['Maestro'].get_all_values()
        cpa_has_formulas = False
        if 'CPA DATA' in worksheets:
            cpa_ws = worksheets['CPA DATA']
            formulas = cpa_ws.get('A2:D2', value_render_option='FORMULA')
            cpa_has_formulas = any(isinstance(v, str) and v.startswith('=') for row in formulas for v in row)
            if cpa_has_formulas:
                values = cpa_ws.get_all_values()
                if not values or values[0][:4] != ['Order', 'Set', 'Prod Line Pieces #', 'ID']:
                    raise ValueError('CPA DATA requiere Order, Set, Prod Line Pieces # e ID.')
                s.import_cpa_sheet([dict(zip(values[0], row)) for row in values[1:]])
        queue_ws = worksheets.get('Kyoodo')
        if queue_ws is None:
            queue_ws = book.add_worksheet('Kyoodo', rows=1000, cols=len(QUEUE_HEADERS))
            worksheets['Kyoodo'] = queue_ws
        remote_queue, current_queue_header = read_remote_queue(queue_ws)
        s.kyoodo.resolve_missing_ids()
        s.kyoodo.merge_results(remote_queue)
        with s.lock:
            meta = dict(s.db.execute('SELECT key,value FROM meta'))
            config_dirty = meta.get('config_dirty') == '1'
            if config_dirty:
                config = [CONFIG_HEADERS] + [[r['external_id'], r['name'], tab_name(dict(r)), r['sort_order'], bool(r['active'])] for r in s.db.execute('SELECT * FROM lasers ORDER BY sort_order,id')]
            else:
                config = worksheets['Configuración'].get_all_values()
            employees, lasers = parse_directory(master, config)
            s.import_directory(employees, lasers)
            if 'Cuentas CPA' in worksheets:
                accounts = worksheets['Cuentas CPA'].get_all_values()
                legacy_accounts = accounts and accounts[0][:3] == ['LASER_ID', 'LASER', 'CUENTA_CPA']
                if accounts and not legacy_accounts and accounts[0][:4] != ['LASER_ID', 'LASER', 'CORREO', 'CONTRASEÑA']:
                    raise ValueError('Cuentas CPA requiere LASER_ID, LASER, CORREO, CONTRASEÑA.')
                pending = set(json.loads(meta.get('cpa_credentials_pending', '[]')))
                with s.db:
                    seen = set()
                    for row in accounts[1:]:
                        row = list(row) + [''] * 4
                        key, account, password = text(row[0]), text(row[2]), str(row[3])
                        if not key:
                            continue
                        if key in seen:
                            raise ValueError('Cuentas CPA contiene un LASER_ID duplicado.')
                        seen.add(key)
                        old = s.db.execute('SELECT id,cpa_account,cpa_password FROM lasers WHERE external_id=?', (key,)).fetchone()
                        if old is None:
                            raise ValueError('Cuentas CPA contiene un LASER_ID que no existe en Configuración.')
                        if key in pending:
                            continue
                        if legacy_accounts:
                            password = old['cpa_password']
                        if old['cpa_account'] != account or old['cpa_password'] != password:
                            s.db.execute('UPDATE lasers SET cpa_account=?,cpa_password=? WHERE id=?', (account, password, old['id']))
                            s._audit('sistema', 'CUENTA_CPA_SHEETS', key)
            queue_jobs = s.kyoodo.jobs()
            if not s.db.execute("SELECT 1 FROM meta WHERE key='dirty' AND value='1'").fetchone() and not needs_queue_push(remote_queue, current_queue_header, queue_jobs):
                with s.db:
                    s.db.execute("INSERT INTO meta VALUES('sync_error','') ON CONFLICT(key) DO UPDATE SET value=''")
                return
            supervisor = s.db.execute("SELECT code FROM users WHERE role='supervisor' AND must_change=0 LIMIT 1").fetchone()
            if not supervisor:
                return
            snapshot = s.snapshot(supervisor[0])
            tables = {table: [dict(r) for r in s.db.execute(f'SELECT * FROM {table}')] for table in TABLES}
            revision = s.db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
        # No sobrescribir reportes manuales o de otra base local.
        if 'Consolidado' in worksheets:
            existing = worksheets['Consolidado'].get_all_values()
            known = {r['request_id'] for r in tables['reports']}
            for row in existing[1:]:
                if any(text(v) for v in row):
                    if len(row) < 10 or text(row[9]) not in known:
                        raise ValueError('Consolidado contiene registros ajenos a esta base local. Recupera el respaldo con --restore-sheet; no se sobrescribió producción.')
        batches, requests = [], []

        def write(title, values):
            ws = worksheets.get(title)
            if ws is None:
                ws = book.add_worksheet(title, rows=max(1000, len(values)), cols=max(13, len(values[0])))
                worksheets[title] = ws
            rows, cols = max(ws.row_count, len(values), 2), max(ws.col_count, len(values[0]))
            if ws.row_count < rows or ws.col_count < cols:
                ws.resize(rows=rows, cols=cols)
            quoted = "'" + title.replace("'", "''") + "'"
            batches.append({'range': f'{quoted}!A1', 'values': values})
            # Limpiar solo valores obsoletos de la proyección, preservando formato y validación.
            if len(values) < ws.row_count:
                requests.append({'repeatCell': {'range': {'sheetId': ws.id, 'startRowIndex': len(values), 'endRowIndex': ws.row_count, 'startColumnIndex': 0, 'endColumnIndex': len(values[0])}, 'cell': {}, 'fields': 'userEnteredValue'}})
            if ws.frozen_row_count != 1:
                ws.freeze(rows=1)
            return ws

        names = {r['code']: r['name'] for r in tables['users']}
        laser_map = {r['id']: r for r in snapshot['lasers']}
        rows = snapshot['reports']
        data = [HEADERS] + [[r['report_date'], r['ord'], r['line'], r['qty'], r['comment'], laser_map[r['laser_id']]['external_id'], r['employee'], names[r['employee']], r['created'], r['request_id'], r['alert'], r['color'], bool(r['reprint'])] for r in rows]
        consolidated = write('Consolidado', data)
        # Las hojas individuales son vistas del consolidado, no copias que puedan divergir.
        for laser in snapshot['lasers']:
            title = tab_name(laser)
            ws = worksheets.get(title)
            if ws is None:
                ws = book.add_worksheet(title, rows=max(1000, len(data)), cols=13)
                worksheets[title] = ws
            needed_rows = max(ws.row_count, len(data))
            if ws.col_count < 13 or ws.row_count < needed_rows:
                ws.resize(rows=needed_rows, cols=max(13, ws.col_count))
            requests.append({'repeatCell': {'range': {'sheetId': ws.id, 'startRowIndex': 1, 'startColumnIndex': 0, 'endColumnIndex': 13}, 'cell': {}, 'fields': 'userEnteredValue'}})
            formula = f'=IFNA(FILTER(Consolidado!A2:M,Consolidado!F2:F="{laser["external_id"]}"),"")'
            requests.append({'updateCells': {'start': {'sheetId': ws.id, 'rowIndex': 0, 'columnIndex': 0}, 'rows': [{'values': [{'userEnteredValue': {'stringValue': h}} for h in HEADERS]}, {'values': [{'userEnteredValue': {'formulaValue': formula}}]}], 'fields': 'userEnteredValue'}})
        # Reglas de color se instalan al preparar el Sheet; nuevas hojas reciben reglas aquí.
        formatted = set(json.loads(meta.get('formatted_tabs', '[]')))
        for title in ['Consolidado'] + [tab_name(l) for l in snapshot['lasers']]:
            ws = worksheets[title]
            if str(ws.id) not in formatted:
                requests += formatting_requests(ws.id)
                formatted.add(str(ws.id))
        push_queue(book, queue_ws, remote_queue, current_queue_header, queue_jobs)
        write('Asignaciones', [['CODIGO_EMPLEADO', 'NOMBRE', 'LASER_ID']] + [[r['code'], r['name'], laser_map[r['laser_id']]['external_id'] if r['laser_id'] in laser_map else ''] for r in snapshot['employees']])
        write('Cuentas CPA', [['LASER_ID', 'LASER', 'CORREO', 'CONTRASEÑA']] + [[r['external_id'], r['name'], r['cpa_account'], r['cpa_password']] for r in snapshot['lasers']])
        if not cpa_has_formulas:
            write('CPA DATA', [['Order', 'Set', 'Prod Line Pieces #', 'ID']] + [[r['ord'], r['line'], r['qty'], r['source_id']] for r in tables['cpa']])
        if config_dirty:
            write('Configuración', config)
        payload = json.dumps({'version': 2, 'tables': tables}, ensure_ascii=False)
        chunks = [payload[i:i + 30000] for i in range(0, len(payload), 30000)]
        write('_Estado', [['Corte SK2 backup v2', 'JSON (no editar)']] + [[i, chunk] for i, chunk in enumerate(chunks)])
        requests.append({'updateSheetProperties': {'properties': {'sheetId': worksheets['_Estado'].id, 'hidden': True}, 'fields': 'hidden'}})
        # Estructura y fórmulas primero; un solo batch RAW para los datos.
        if requests:
            book.batch_update({'requests': requests})
        book.values_batch_update({'valueInputOption': 'RAW', 'data': batches})
        with s.lock, s.db:
            current = s.db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
            if current == revision:
                s.db.execute("UPDATE meta SET value='0' WHERE key='dirty'")
                s.db.execute("UPDATE meta SET value='0' WHERE key='config_dirty'")
                s.db.execute("UPDATE meta SET value='[]' WHERE key='cpa_credentials_pending'")
            for key, value in [('sync_at', stamp()), ('sync_error', ''), ('formatted_tabs', json.dumps(sorted(formatted)))]:
                s.db.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))
    def restore_empty(self):
        s = self.store
        with s.lock:
            if s.db.execute('SELECT 1 FROM users').fetchone():
                raise ValueError('La recuperación solo se permite sobre una base local vacía.')
        cells = self.book().worksheet('_Estado').get_all_values()
        payload = json.loads(''.join(row[1] for row in cells[1:] if len(row) > 1))
        if payload.get('version') not in (1, 2) or set(payload['tables']) not in (set(TABLES), set(LEGACY_TABLES)):
            raise ValueError('Respaldo no compatible.')
        with s.lock, s.db:
            for table in TABLES:
                allowed = {r[1] for r in s.db.execute(f'PRAGMA table_info({table})')}
                for row in payload['tables'].get(table, []):
                    if not set(row) <= allowed:
                        raise ValueError('Columnas de respaldo inválidas.')
                    columns = list(row)
                    s.db.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", [row[k] for k in columns])
            s.kyoodo.ensure_jobs()
            s._audit('sistema', 'RESTAURACION', 'Recuperado desde Google Sheets')

    def run(self):
        while True:
            self.sync()
            time.sleep(60)


def formatting_requests(sid):
    green, black = {'red': .22, 'green': 1, 'blue': .53}, {'red': .027, 'green': .047, 'blue': .039}
    requests = [
        {'repeatCell': {'range': {'sheetId': sid, 'startRowIndex': 0, 'endRowIndex': 1, 'startColumnIndex': 0, 'endColumnIndex': 13}, 'cell': {'userEnteredFormat': {'backgroundColor': black, 'textFormat': {'foregroundColor': green, 'bold': True}, 'wrapStrategy': 'WRAP'}}, 'fields': 'userEnteredFormat'}},
        {'updateSheetProperties': {'properties': {'sheetId': sid, 'gridProperties': {'frozenRowCount': 1}}, 'fields': 'gridProperties.frozenRowCount'}},
        {'updateDimensionProperties': {'range': {'sheetId': sid, 'dimension': 'COLUMNS', 'startIndex': 10, 'endIndex': 11}, 'properties': {'pixelSize': 420}, 'fields': 'pixelSize'}},
        {'updateDimensionProperties': {'range': {'sheetId': sid, 'dimension': 'COLUMNS', 'startIndex': 11, 'endIndex': 13}, 'properties': {'hiddenByUser': True}, 'fields': 'hiddenByUser'}},
    ]
    for color, rgb in [('red', {'red': 1, 'green': .72, 'blue': .72}), ('yellow', {'red': 1, 'green': .92, 'blue': .55})]:
        requests.append({'addConditionalFormatRule': {'index': 0, 'rule': {'ranges': [{'sheetId': sid, 'startRowIndex': 1, 'startColumnIndex': 0, 'endColumnIndex': 13}], 'booleanRule': {'condition': {'type': 'CUSTOM_FORMULA', 'values': [{'userEnteredValue': f'=$L2="{color}"'}]}, 'format': {'backgroundColor': rgb, 'textFormat': {'foregroundColor': {'red': .1, 'green': .05, 'blue': .05}}}}}}})
    return requests

