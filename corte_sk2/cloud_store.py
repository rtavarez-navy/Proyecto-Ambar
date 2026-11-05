"""Base central en Google Sheets. Sin SQLite ni archivos de datos en las PC."""
import hashlib
import json
import secrets
import time
from datetime import date
from .core import DEFAULT_PASSWORD, identifier, matches, normalize_cpa, password_hash, pieces, stamp, text
from .kyoodo import BLOCKED, DONE, MISSING_ID, PENDING, QUEUE_HEADERS, RETRY
from .sheets import CONFIG_HEADERS, HEADERS, parse_directory

PROCESSING = 'EN PROCESO'
CLOUD_QUEUE_HEADERS = QUEUE_HEADERS + ['CLAIM_ID', 'CLAIM_EXPIRA', 'INTENTOS', 'CLAIM_REQUEST_ID']
USERS = ['CODIGO_EMPLEADO', 'NOMBRE', 'PASSWORD_HASH', 'ROL', 'CAMBIAR_CLAVE', 'VERSION_CLAVE']
SESSIONS = ['TOKEN_HASH', 'CODIGO_EMPLEADO', 'EXPIRA', 'VERSION_CLAVE']
ATTEMPTS = ['CLAVE', 'INTENTOS_JSON']
AUDIT = ['FECHA', 'ACTOR', 'ACCION', 'DETALLE']
SCHEMA = {'_Usuarios': USERS, '_Sesiones': SESSIONS, '_Accesos': ATTEMPTS, '_Auditoria': AUDIT,
          'Consolidado': HEADERS, 'Kyoodo': CLOUD_QUEUE_HEADERS,
          'Asignaciones': ['CODIGO_EMPLEADO', 'NOMBRE', 'LASER_ID'],
          'Cuentas CPA': ['LASER_ID', 'LASER', 'CORREO', 'CONTRASEÑA'],
          'CPA DATA': ['Order', 'Set', 'Prod Line Pieces #', 'ID']}
READS = ['Maestro', 'Configuración'] + list(SCHEMA)


def quote(title):
    return "'" + title.replace("'", "''") + "'"


def order_key(value):
    order = identifier(value)
    if order.upper().startswith('SO-') and order[3:].isdigit():
        return order[3:]
    return order


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class SheetsDatabase:
    def __init__(self, book):
        self.book = book
        self.tabs = {w.title: w for w in book.worksheets()}

    def initialize(self, supervisor_code='', supervisor_hash=''):
        if not {'Maestro', 'Configuración'} <= self.tabs.keys():
            raise ValueError('El Sheet debe contener Maestro y Configuración.')
        legacy = None
        fresh_users = '_Usuarios' not in self.tabs or len(self.tabs['_Usuarios'].get_all_values()) <= 1
        if fresh_users and '_Estado' in self.tabs:
            cells = self.tabs['_Estado'].get_all_values()
            if len(cells) > 1:
                legacy = json.loads(''.join(str(r[1]) for r in cells[1:] if len(r) > 1))
        updates, hidden = [], []
        for title, headers in SCHEMA.items():
            ws = self.tabs.get(title)
            if ws is None:
                ws = self.book.add_worksheet(title, rows=1000, cols=max(len(headers), 16))
                self.tabs[title] = ws
                updates.append({'range': f'{quote(title)}!A1', 'values': [headers]})
            elif ws.col_count < len(headers):
                ws.resize(rows=ws.row_count, cols=len(headers))
            if title.startswith('_'):
                hidden.append({'updateSheetProperties': {'properties': {'sheetId': ws.id, 'hidden': True}, 'fields': 'hidden'}})
        # Añadir reservas de Kyoodo sin tocar filas ni estados existentes.
        updates.append({'range': "'Kyoodo'!A1", 'values': [CLOUD_QUEUE_HEADERS]})
        if fresh_users:
            users, assignments = [], []
            if legacy:
                tables = legacy.get('tables', {})
                lasers = {r['id']: r['external_id'] for r in tables.get('lasers', [])}
                for r in tables.get('users', []):
                    users.append([r['code'], r['name'], r['password'], r['role'], int(r['must_change']), 1])
                    if r.get('laser_id') in lasers:
                        assignments.append([r['code'], r['name'], lasers[r['laser_id']]])
            if not users:
                if not supervisor_code or not supervisor_hash:
                    raise ValueError('Configura el supervisor inicial antes de publicar el servicio.')
                users = [[supervisor_code, 'Supervisor', supervisor_hash, 'supervisor', 1, 1]]
            updates.append({'range': "'_Usuarios'!A1", 'values': [USERS] + users})
            if assignments and len(self.tabs['Asignaciones'].get_all_values()) <= 1:
                updates.append({'range': "'Asignaciones'!A1", 'values': [SCHEMA['Asignaciones']] + assignments})
        if updates:
            self.book.values_batch_update({'valueInputOption': 'RAW', 'data': updates})
        if hidden:
            self.book.batch_update({'requests': hidden})

    def load(self):
        ranges = [f'{quote(title)}!A:{chr(64 + (3 if title == "Maestro" else 5 if title == "Configuración" else len(SCHEMA[title])))}' for title in READS]
        response = self.book.values_batch_get(ranges=ranges, params={'valueRenderOption': 'UNFORMATTED_VALUE'})
        self.tables = {}
        for title, data in zip(READS, response['valueRanges']):
            values = data.get('values', [])
            headers = ['APELLIDOS_NOMBRES', 'CODIGO_EMPLEADO', 'DEPARTAMENTO'] if title == 'Maestro' else CONFIG_HEADERS if title == 'Configuración' else SCHEMA[title]
            if not values or values[0][:len(headers)] != headers:
                raise ValueError(f'Cabecera no compatible en {title}.')
            rows = [list(row) + [''] * (len(headers) - len(row)) for row in values[1:] if any(str(v).strip() for v in row)]
            self.tables[title] = [row[:len(headers)] for row in rows]
        self.master, self.lasers = parse_directory([['APELLIDOS_NOMBRES', 'CODIGO_EMPLEADO', 'DEPARTAMENTO']] + self.tables['Maestro'], [CONFIG_HEADERS] + self.tables['Configuración'])
        self.master = dict(self.master)
        self.lasers = {r['external_id']: r for r in self.lasers}
        self.cpa = {(o, line): (qty, rid) for o, line, qty, rid in normalize_cpa([{**dict(zip(SCHEMA['CPA DATA'], r)), 'Order': order_key(r[0])} for r in self.tables['CPA DATA']], include_ids=True)} if self.tables['CPA DATA'] else {}
        self.changes = []

    def _set(self, title, rows):
        old_size = len(self.tables[title])
        self.tables[title] = rows
        headers = SCHEMA.get(title, CONFIG_HEADERS)
        values = [headers] + rows + [[''] * len(headers) for _ in range(max(0, old_size - len(rows)))]
        self._change(title, 1, 1, values)

    def _change(self, title, row, col, values):
        ws = self.tabs[title]
        need_rows, need_cols = row - 1 + len(values), col - 1 + max(map(len, values))
        if need_rows > ws.row_count or need_cols > ws.col_count:
            ws.resize(rows=max(ws.row_count, need_rows + 100), cols=max(ws.col_count, need_cols))
        self.changes.append({'range': f'{quote(title)}!{chr(64 + col)}{row}', 'values': values})

    def _append(self, title, row):
        self.tables[title].append(row)
        self._change(title, len(self.tables[title]) + 1, 1, [row])

    def _audit(self, actor, action, detail=''):
        self._append('_Auditoria', [stamp(), actor, action, detail])

    def commit(self):
        if self.changes:
            self.book.values_batch_update({'valueInputOption': 'RAW', 'data': self.changes})
            self.changes = []

    def users(self):
        users = {str(r[0]): {'code': str(r[0]), 'name': r[1], 'password': r[2], 'role': r[3], 'must_change': int(r[4]), 'version': int(r[5])} for r in self.tables['_Usuarios']}
        for code, name in self.master.items():
            if code not in users:
                users[code] = {'code': code, 'name': name, 'password': '', 'role': 'operator', 'must_change': 1, 'version': 1}
            elif users[code]['role'] == 'operator':
                users[code]['name'] = name
        assigned = {str(r[0]): str(r[2]) for r in self.tables['Asignaciones']}
        for code, user in users.items():
            laser = assigned.get(code)
            user['laser_id'] = laser if laser in self.lasers and self.lasers[laser]['active'] else None
        return users

    @staticmethod
    def public_user(user):
        return {k: user[k] for k in ('code', 'name', 'role', 'must_change', 'laser_id')}

    def save_user(self, user):
        rows = [r for r in self.tables['_Usuarios'] if str(r[0]) != user['code']]
        rows.append([user['code'], user['name'], user['password'], user['role'], user['must_change'], user['version']])
        self._set('_Usuarios', rows)

    def login(self, data):
        code, password = text(data.get('code')), str(data.get('password', ''))
        if not code or len(code) > 60:
            raise ValueError('Código o clave incorrectos.')
        now, key = time.time(), digest(code)
        attempts = {str(r[0]): json.loads(r[1]) for r in self.tables['_Accesos']}
        history = [t for t in attempts.get(key, []) if now - t < 300]
        if len(history) >= 10:
            raise ValueError('Demasiados intentos. Espera cinco minutos.')
        user = self.users().get(code)
        valid = user and (matches(password, user['password']) if user['password'] else secrets.compare_digest(password, DEFAULT_PASSWORD))
        attempts[key] = [] if valid else history + [now]
        self._set('_Accesos', [[k, json.dumps(v)] for k, v in attempts.items() if v and now - v[-1] < 300])
        if not valid:
            raise ValueError('Código o clave incorrectos.')
        if not user['password']:
            user['password'] = password_hash(DEFAULT_PASSWORD)
            self.save_user(user)
        token = secrets.token_urlsafe(32)
        sessions = [r for r in self.tables['_Sesiones'] if float(r[2]) > now]
        sessions.append([digest(token), code, now + 8 * 3600, user['version']])
        self._set('_Sesiones', sessions)
        return {'token': token, 'user': self.public_user(user)}

    def auth(self, token):
        if not token:
            raise PermissionError('Inicia sesión.')
        session = next((r for r in self.tables['_Sesiones'] if r[0] == digest(token)), None)
        user = self.users().get(str(session[1])) if session else None
        if not user or float(session[2]) <= time.time() or int(session[3]) != user['version']:
            raise PermissionError('Sesión vencida. Inicia sesión otra vez.')
        return user

    def supervisor(self, user):
        if user['role'] != 'supervisor':
            raise PermissionError('Solo el supervisor puede realizar esta operación.')

    def laser(self, user, requested=None):
        key = text(requested) if user['role'] == 'supervisor' else user['laser_id']
        if key not in self.lasers or not self.lasers[key]['active']:
            raise ValueError('Esperando a ser asignado. Habla con tu supervisor.')
        return key

    def accounts(self):
        return {str(r[0]): (str(r[2]), str(r[3])) for r in self.tables['Cuentas CPA']}

    def reports(self):
        grouped = {}
        rows = []
        for values in self.tables['Consolidado']:
            if not values[9]:
                raise ValueError('Consolidado contiene una fila sin ID_REGISTRO.')
            row = {'report_date': str(values[0]), 'ord': identifier(values[1]), 'line': str(values[2]).upper(), 'qty': pieces(values[3]), 'comment': str(values[4]), 'laser_id': str(values[5]), 'employee': str(values[6]), 'created': str(values[8]), 'id': str(values[9]), 'reprint': bool(values[12])}
            row['laser'] = self.lasers.get(row['laser_id'], {}).get('name', row['laser_id'])
            rows.append(row)
            grouped.setdefault((order_key(row['ord']), row['line']), []).append(row)
        totals = {k: sum(r['qty'] for r in group) for k, group in grouped.items()}
        for row in rows:
            key = (order_key(row['ord']), row['line'])
            row['duplicate'], row['missing'] = len(grouped[key]) > 1, key not in self.cpa
            row['excess'] = key in self.cpa and totals[key] > self.cpa[key][0]
            messages = []
            if row['duplicate']:
                messages.append('Duplicada: ' + ', '.join(sorted({r['laser'] for r in grouped[key]})))
            if row['excess']:
                messages.append(f'Orden adulterada · Acumulado {totals[key]} / CPA {self.cpa[key][0]}')
            if row['missing']:
                messages.append('La orden no existe en el CPA. Revisa el # de orden y la línea.')
            row['alert'] = ' | '.join(messages)
            row['color'] = 'red' if row['duplicate'] or row['excess'] else 'yellow' if row['missing'] else ''
        return rows

    def snapshot(self, user):
        supervisor = user['role'] == 'supervisor'
        accounts = self.accounts()
        lasers = []
        for key, item in sorted(self.lasers.items(), key=lambda pair: pair[1]['sort_order']):
            if supervisor:
                lasers.append({**item, 'id': key, 'cpa_account': accounts.get(key, ('', ''))[0], 'cpa_password': accounts.get(key, ('', ''))[1]})
            elif key == user['laser_id']:
                lasers.append({'id': key, 'name': item['name']})
        rows = self.reports()
        return {'user': self.public_user(user), 'reports': list(reversed(rows if supervisor else [r for r in rows if r['laser_id'] == user['laser_id']])), 'lasers': lasers,
                'employees': [self.public_user(r) for r in self.users().values() if r['role'] == 'operator'] if supervisor else [],
                'today': stamp()[:10], 'cpa_lines': len(self.cpa), 'cpa_updated': 'Google Sheets', 'sync': {'dirty': '0', 'sync_at': stamp(), 'sync_error': ''}, 'backend': 'google-sheets'}

    def refresh_alerts(self):
        for index, row in enumerate(self.reports()):
            values = self.tables['Consolidado'][index]
            if values[10:12] != [row['alert'], row['color']]:
                values[10:12] = [row['alert'], row['color']]
                self._change('Consolidado', index + 2, 11, [[row['alert'], row['color']]])

    def ensure_views(self):
        for key, laser in self.lasers.items():
            title = laser['sheet_title']
            ws = self.tabs.get(title)
            if ws is None:
                ws = self.book.add_worksheet(title, rows=1000, cols=13)
                self.tabs[title] = ws
                self._change(title, 1, 1, [HEADERS])
                self.book.batch_update({'requests': [{'updateCells': {'start': {'sheetId': ws.id, 'rowIndex': 1, 'columnIndex': 0}, 'rows': [{'values': [{'userEnteredValue': {'formulaValue': f'=IFNA(FILTER(Consolidado!A2:M,Consolidado!F2:F="{key}"),"")'}}]}], 'fields': 'userEnteredValue'}}]})
            if ws.row_count < len(self.tables['Consolidado']) + 1:
                ws.resize(rows=len(self.tables['Consolidado']) + 101, cols=max(13, ws.col_count))

    def report(self, user, data):
        key = text(data.get('request_id'))
        if not key or len(key) > 128:
            raise ValueError('Identificador de envío inválido.')
        old = next((r for r in self.tables['Consolidado'] if str(r[9]) == key), None)
        if old:
            if str(old[6]) != user['code']:
                raise PermissionError('Identificador de envío ajeno.')
            return {'id': key}
        laser = self.laser(user, data.get('laser_id'))
        order, line, qty = order_key(data.get('order')), identifier(data.get('line')).upper(), pieces(data.get('qty'))
        report_date = text(data.get('report_date')) or stamp()[:10]
        if date.fromisoformat(report_date).isoformat() != report_date or not order or not line or qty < 1:
            raise ValueError('Revisa fecha, orden, línea y piezas.')
        comment = str(data.get('comment', ''))
        if len(order) > 60 or len(line) > 20 or len(comment) > 2000:
            raise ValueError('Los datos superan el tamaño permitido.')
        created, cpa_id = stamp(), self.cpa.get((order, line), (0, ''))[1]
        seq = max([int(r[2]) for r in self.tables['Kyoodo']] or [0]) + 1
        self._append('Consolidado', [report_date, order, line, qty, comment, laser, user['code'], user['name'], created, key, '', '', bool(data.get('reprint'))])
        self._append('Kyoodo', [order, self.lasers[laser]['name'], seq, '' if cpa_id else MISSING_ID, line, key, cpa_id, PENDING if cpa_id else BLOCKED, order + '|' + line, created, 910, laser, '', 0, 0, ''])
        self.refresh_alerts()
        self.ensure_views()
        self._audit(user['code'], 'REPORTE', key)
        return {'id': key, 'snapshot': self.snapshot(user)}

    def reconcile_queue(self):
        for i, row in enumerate(self.tables['Kyoodo']):
            if row[7] == BLOCKED and not row[6]:
                rid = self.cpa.get((order_key(row[0]), str(row[4]).upper()), (0, ''))[1]
                if rid:
                    row[6:8] = [rid, PENDING]
                    if row[3] == MISSING_ID:
                        row[3] = ''
                    self._change('Kyoodo', i + 2, 4, [row[3:8]])

    def kyoodo_next(self, user, data):
        laser, now = self.laser(user), time.time()
        self.reconcile_queue()
        rows = sorted(enumerate(self.tables['Kyoodo']), key=lambda pair: int(pair[1][2]))
        own = [(i, r) for i, r in rows if str(r[11]) == laser]
        busy = next((r for _, r in own if r[7] == PROCESSING and float(r[13] or 0) > now), None)
        claim_request = text(data.get('request_id'))
        if busy is not None and (not claim_request or claim_request != str(busy[15])):
            return {'job': None, 'busy': True}
        account, password = self.accounts().get(laser, ('', ''))
        if not account or not password:
            raise ValueError('Falta el correo o la contraseña CPA de esta láser.')
        if busy is not None:
            return {'job': dict(zip(CLOUD_QUEUE_HEADERS, busy)), 'account': {'email': account, 'password': password}}
        for i, row in own:
            if row[6] and (row[7] in (PENDING, RETRY) or row[7] == PROCESSING and float(row[13] or 0) <= now):
                claim = secrets.token_urlsafe(24)
                row[7], row[12], row[13], row[14], row[15] = PROCESSING, claim, now + 600, int(row[14] or 0) + 1, claim_request or secrets.token_urlsafe(24)
                self._change('Kyoodo', i + 2, 8, [row[7:]])
                return {'job': dict(zip(CLOUD_QUEUE_HEADERS, row)), 'account': {'email': account, 'password': password}}
        return {'job': None, 'busy': False}

    def kyoodo_result(self, user, data):
        laser = self.laser(user)
        key, claim = text(data.get('request_id')), text(data.get('claim_id'))
        entry = next(((i, r) for i, r in enumerate(self.tables['Kyoodo']) if str(r[5]) == key), None)
        if not entry or str(entry[1][11]) != laser:
            raise PermissionError('Ese trabajo no pertenece a tu láser.')
        i, row = entry
        if row[7] == DONE:
            return {'ok': True, 'state': DONE}
        if row[7] != PROCESSING or not claim or not secrets.compare_digest(claim, str(row[12])):
            raise PermissionError('La reserva del trabajo ya no es válida.')
        state = text(data.get('state'))
        if state not in (RETRY, DONE):
            raise ValueError('El resultado debe ser REINTENTAR o ACTUALIZADA A 910.')
        row[3], row[7], row[12], row[13] = str(data.get('comment', ''))[:2000], state, '', 0
        self._change('Kyoodo', i + 2, 4, [[row[3]]])
        self._change('Kyoodo', i + 2, 8, [row[7:]])
        self._audit(user['code'], 'KYOODO_RESULTADO', key + ': ' + state)
        return {'ok': True, 'state': state}

    def call(self, path, data, token=''):
        if path == '/login':
            return self.login(data)
        user = self.auth(token)
        if path == '/logout':
            self._set('_Sesiones', [r for r in self.tables['_Sesiones'] if r[0] != digest(token)])
            return {'ok': True}
        if path == '/password':
            old, new = str(data.get('old', '')), str(data.get('new', ''))
            if not user['password'] or not matches(old, user['password']):
                raise ValueError('La clave anterior es incorrecta.')
            if len(new) < 8 or new in (DEFAULT_PASSWORD, old):
                raise ValueError('Usa una clave nueva de al menos 8 caracteres.')
            user['password'], user['must_change'], user['version'] = password_hash(new), 0, user['version'] + 1
            self.save_user(user)
            self._set('_Sesiones', [[r[0], r[1], r[2], user['version']] if r[0] == digest(token) else r for r in self.tables['_Sesiones'] if str(r[1]) != user['code'] or r[0] == digest(token)])
            self._audit(user['code'], 'CLAVE')
            return {'ok': True}
        if user['must_change']:
            raise ValueError('Primero debes cambiar tu clave.')
        if path == '/snapshot':
            return self.snapshot(user)
        if path == '/report':
            return self.report(user, data)
        if path == '/kyoodo/next':
            return self.kyoodo_next(user, data)
        if path == '/kyoodo/result':
            return self.kyoodo_result(user, data)
        self.supervisor(user)
        if path == '/employee':
            code, name = text(data.get('code')), text(data.get('name'))
            if not code or not name or code in self.users():
                raise ValueError('Completa código/nombre nuevos; el código no debe existir.')
            self.save_user({'code': code, 'name': name, 'password': password_hash(DEFAULT_PASSWORD), 'role': 'operator', 'must_change': 1, 'version': 1})
        elif path == '/assign':
            code, laser = text(data.get('code')), data.get('laser_id')
            employee = self.users().get(code)
            if not employee or employee['role'] != 'operator':
                raise ValueError('Selecciona un operador válido.')
            if laser is not None:
                laser = self.laser(user, laser)
            self._set('Asignaciones', [r for r in self.tables['Asignaciones'] if str(r[0]) != code] + ([[code, employee['name'], laser]] if laser else []))
        elif path in ('/laser', '/laser-active'):
            key = text(data.get('laser_id'))
            if path == '/laser-active':
                if key not in self.lasers:
                    raise ValueError('Láser inexistente.')
                self.lasers[key]['active'] = int(bool(data.get('active', True)))
                if not self.lasers[key]['active']:
                    self._set('Asignaciones', [r for r in self.tables['Asignaciones'] if str(r[2]) != key])
            else:
                name = text(data.get('name'))
                if not name or len(name) > 60 or any(v['name'] == name and k != key for k, v in self.lasers.items()):
                    raise ValueError('Nombre de láser inválido o duplicado.')
                if not key:
                    n = 1
                    while f'LASER_{n:02d}' in self.lasers:
                        n += 1
                    key = f'LASER_{n:02d}'
                    self.lasers[key] = {'name': name, 'sheet_title': f'Láser {n:02d}', 'sort_order': n, 'active': 1}
                elif key not in self.lasers:
                    raise ValueError('Láser inexistente.')
                self.lasers[key]['name'] = name
                old = self.accounts().get(key, ('', ''))
                password = str(data['cpa_password']) if data.get('cpa_password') is not None else old[1]
                self._set('Cuentas CPA', [r for r in self.tables['Cuentas CPA'] if str(r[0]) != key] + [[key, name, text(data.get('account')), password]])
            self._set('Configuración', [[k, r['name'], r['sheet_title'], r['sort_order'], bool(r['active'])] for k, r in self.lasers.items()])
            self.ensure_views()
        elif path == '/cpa':
            formulas = self.tabs['CPA DATA'].get('A2:D2', value_render_option='FORMULA')
            if any(str(v).startswith('=') for row in formulas for v in row):
                raise ValueError('CPA DATA está conectada por fórmulas. Actualiza el archivo de origen para conservar esa conexión.')
            rows = normalize_cpa([{**row, 'Order': order_key(row.get('Order', row.get('Orden', '')))} for row in data.get('rows', [])], include_ids=True)
            self._set('CPA DATA', [[o, line, qty, rid] for o, line, qty, rid in rows])
            self.cpa = {(order_key(o), line): (qty, rid) for o, line, qty, rid in rows}
            self.reconcile_queue()
            self.refresh_alerts()
            self._audit(user['code'], 'CPA', str(len(rows)))
            return {'count': len(rows)}
        elif path == '/sync':
            self.reconcile_queue()
            self.refresh_alerts()
            self.ensure_views()
        else:
            raise ValueError('Operación desconocida.')
        self._audit(user['code'], path)
        return {'ok': True}
