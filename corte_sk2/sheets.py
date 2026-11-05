"""Proyección recuperable de datos a Google Sheets. Solo se ejecuta en el servidor."""
import json
import threading
import time
from .core import stamp

TABLES = ('lasers', 'users', 'cpa', 'reports', 'audit')


def tab_name(laser):
    # El id estable evita colisiones y títulos inválidos de Sheets.
    return f"Laser {laser['id']}"


class SheetsSync:
    def __init__(self, store, spreadsheet_id, credentials):
        self.store = store
        self.spreadsheet_id = spreadsheet_id
        self.credentials = credentials
        self.guard = threading.Lock()

    def book(self):
        import gspread
        return gspread.service_account(filename=self.credentials).open_by_key(self.spreadsheet_id)

    def sync(self):
        if not self.guard.acquire(blocking=False):
            return
        try:
            self._sync()
        except Exception as exc:
            with self.store.lock, self.store.db:
                self.store.db.execute("INSERT INTO meta VALUES('sync_error',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (f'{type(exc).__name__}: no se pudo sincronizar con Google Sheets.',))
        finally:
            self.guard.release()

    def _sync(self):
        s = self.store
        with s.lock:
            if not s.db.execute("SELECT 1 FROM meta WHERE key='dirty' AND value='1'").fetchone():
                return
            supervisor = s.db.execute("SELECT code FROM users WHERE role='supervisor' AND must_change=0 LIMIT 1").fetchone()
            if not supervisor:
                return
            snapshot = s.snapshot(supervisor[0])
            tables = {table: [dict(r) for r in s.db.execute(f'SELECT * FROM {table}')] for table in TABLES}
            revision = s.db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
        book = self.book()
        worksheets = {w.title: w for w in book.worksheets()}

        def write(title, values, colors=None):
            ws = worksheets.get(title)
            if ws is None:
                ws = book.add_worksheet(title, rows=max(100, len(values)), cols=max(12, len(values[0])))
                worksheets[title] = ws
            target_rows, target_cols = max(2, len(values)), max(1, len(values[0]))
            if ws.row_count != target_rows or ws.col_count != target_cols:
                ws.resize(rows=target_rows, cols=target_cols)
            # RAW impide interpretar comentarios como fórmulas.
            ws.update(values=values, range_name='A1', value_input_option='RAW')
            if colors is not None:
                requests = [{'repeatCell': {'range': {'sheetId': ws.id, 'startRowIndex': 1}, 'cell': {'userEnteredFormat': {'backgroundColor': {'red': 0.04, 'green': 0.08, 'blue': 0.06}}}, 'fields': 'userEnteredFormat.backgroundColor'}}]
                for i, color in enumerate(colors, 1):
                    if color:
                        rgb = {'red': 0.8, 'green': 0.2, 'blue': 0.2} if color == 'red' else {'red': 1, 'green': 0.85, 'blue': 0.25}
                        requests.append({'repeatCell': {'range': {'sheetId': ws.id, 'startRowIndex': i, 'endRowIndex': i + 1}, 'cell': {'userEnteredFormat': {'backgroundColor': rgb}}, 'fields': 'userEnteredFormat.backgroundColor'}})
                book.batch_update({'requests': requests})
            if ws.frozen_row_count != 1:
                ws.freeze(rows=1)

        for laser in snapshot['lasers']:
            rows = [r for r in snapshot['reports'] if r['laser_id'] == laser['id']]
            values = [['Fecha y hora', 'Orden', 'Línea', 'Piezas', 'Comentarios', 'Reprint', 'Empleado', 'Láser', 'Validación', 'ID']]
            values += [[r['created'], r['ord'], r['line'], r['qty'], r['comment'], r['reprint'], r['employee'], r['laser'], r['alert'], r['id']] for r in rows]
            write(tab_name(laser), values, [r['color'] for r in rows])
        rows = sorted(snapshot['reports'], key=lambda r: (int(r['ord']) if r['ord'].isdigit() else -1, r['ord'], r['id']), reverse=True)
        write('Kyoodo', [['Orden', 'Láser', 'Secuencia', 'Comentario', 'Línea', 'ID']] + [[r['ord'], r['laser'], i, ' | '.join(filter(None, [r['comment'], r['alert']])), r['line'], r['id']] for i, r in enumerate(rows, 1)])
        names = {r['id']: r['name'] for r in snapshot['lasers']}
        write('Asignaciones', [['Código', 'Nombre', 'Láser']] + [[r['code'], r['name'], names.get(r['laser_id'], '')] for r in snapshot['employees']])
        write('Cuentas CPA', [['Láser', 'Cuenta CPA']] + [[r['name'], r['cpa_account']] for r in snapshot['lasers']])
        write('CPA DATA', [['Order', 'Set', 'Prod Line Pieces #']] + [[r['ord'], r['line'], r['qty']] for r in tables['cpa']])
        # Recuperación completa; no compartir este Sheet con operadores.
        payload = json.dumps({'version': 1, 'tables': tables}, ensure_ascii=False)
        chunks = [payload[i:i + 30000] for i in range(0, len(payload), 30000)]
        write('_Estado', [['Corte SK2 backup v1', 'JSON (no editar)']] + [[i, chunk] for i, chunk in enumerate(chunks)])
        book.batch_update({'requests': [{'updateSheetProperties': {'properties': {'sheetId': worksheets['_Estado'].id, 'hidden': True}, 'fields': 'hidden'}}]})
        with s.lock, s.db:
            current = s.db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
            if current == revision:
                s.db.execute("UPDATE meta SET value='0' WHERE key='dirty'")
            for key, value in [('sync_at', stamp()), ('sync_error', '')]:
                s.db.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))

    def restore_empty(self):
        s = self.store
        with s.lock:
            if s.db.execute('SELECT 1 FROM users').fetchone():
                raise ValueError('La recuperación solo se permite sobre una base local vacía.')
        cells = self.book().worksheet('_Estado').get_all_values()
        payload = json.loads(''.join(row[1] for row in cells[1:] if len(row) > 1))
        if payload.get('version') != 1 or set(payload['tables']) != set(TABLES):
            raise ValueError('Respaldo no compatible.')
        with s.lock, s.db:
            for table in TABLES:
                allowed = {r[1] for r in s.db.execute(f'PRAGMA table_info({table})')}
                for row in payload['tables'][table]:
                    if not set(row) <= allowed:
                        raise ValueError('Columnas de respaldo inválidas.')
                    columns = list(row)
                    s.db.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", [row[k] for k in columns])
            s._audit('sistema', 'RESTAURACION', 'Recuperado desde Google Sheets')

    def run(self):
        while True:
            self.sync()
            time.sleep(60)
