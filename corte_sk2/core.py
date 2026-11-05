"""Reglas, autenticación y almacenamiento transaccional del servicio central."""
import csv
import hashlib
import hmac
import io
import json
import secrets
import sqlite3
import threading
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

TZ = ZoneInfo('America/Santo_Domingo')
DEFAULT_PASSWORD = '123456'


def stamp():
    return datetime.now(TZ).isoformat(timespec='seconds')


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 260000).hex()
    return f'{salt}:{digest}'


def matches(password, encoded):
    return hmac.compare_digest(password_hash(password, encoded.split(':')[0]), encoded)


def text(value):
    return str(value if value is not None else '').strip()


def identifier(value):
    value = text(value)
    if value.endswith('.0') and value[:-2].isdigit():
        return value[:-2]
    return value


def pieces(value):
    try:
        number = Decimal(text(value).replace(',', ''))
        if not number.is_finite() or number < 0 or number != number.to_integral_value():
            raise ValueError('Las piezas deben ser un número entero mayor o igual a cero.')
        return int(number)
    except InvalidOperation:
        raise ValueError('Cantidad de piezas inválida.') from None


def normalize_cpa(rows):
    result = {}
    for index, row in enumerate(rows, 2):
        order = identifier(row.get('Order', row.get('Orden', '')))
        line = identifier(row.get('Set', row.get('Linea', row.get('Línea', '')))).upper()
        raw = row.get('Prod Line Pieces #')
        if raw is None or text(raw) == '':
            raw = row.get('Qty')
        if not order and not line:
            continue
        if not order or not line or raw is None:
            raise ValueError(f'Fila {index}: se requieren Order, Set y Prod Line Pieces # o Qty.')
        qty = pieces(raw)
        key = (order, line)
        if key in result and result[key] != qty:
            raise ValueError(f'CPA ambiguo: {order}|{line} tiene cantidades distintas.')
        result[key] = qty
    if not result:
        raise ValueError('La base CPA no contiene líneas válidas. No se reemplazó la base anterior.')
    return [(order, line, qty) for (order, line), qty in result.items()]


class Store:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS lasers(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, cpa_account TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS users(code TEXT PRIMARY KEY, name TEXT NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL, must_change INTEGER NOT NULL DEFAULT 1, laser_id INTEGER REFERENCES lasers(id));
        CREATE TABLE IF NOT EXISTS cpa(ord TEXT NOT NULL, line TEXT NOT NULL, qty INTEGER NOT NULL, PRIMARY KEY(ord,line));
        CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT UNIQUE NOT NULL, created TEXT NOT NULL, employee TEXT NOT NULL REFERENCES users(code), laser_id INTEGER NOT NULL REFERENCES lasers(id), ord TEXT NOT NULL, line TEXT NOT NULL, qty INTEGER NOT NULL, comment TEXT NOT NULL, reprint INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS report_key ON reports(ord,line);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, created TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL);
        ''')
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(lasers)')}
        for name, definition in [('external_id', "TEXT NOT NULL DEFAULT ''"), ('sheet_title', "TEXT NOT NULL DEFAULT ''"), ('active', 'INTEGER NOT NULL DEFAULT 1'), ('sort_order', 'INTEGER NOT NULL DEFAULT 0')]:
            if name not in columns:
                self.db.execute(f'ALTER TABLE lasers ADD COLUMN {name} {definition}')
        for row in self.db.execute("SELECT id FROM lasers WHERE external_id=''").fetchall():
            self.db.execute('UPDATE lasers SET external_id=?, sheet_title=?, sort_order=? WHERE id=?', (f'LASER_{row[0]:02d}', f'Láser {row[0]:02d}', row[0], row[0]))
        if 'report_date' not in {r[1] for r in self.db.execute('PRAGMA table_info(reports)')}:
            self.db.execute("ALTER TABLE reports ADD COLUMN report_date TEXT NOT NULL DEFAULT ''")
        self.db.commit()

    def _audit(self, actor, action, detail):
        self.db.execute('INSERT INTO audit(created,actor,action,detail) VALUES(?,?,?,?)', (stamp(), actor, action, detail))
        self.db.execute("INSERT INTO meta VALUES('dirty','1') ON CONFLICT(key) DO UPDATE SET value='1'")
        self.db.execute("INSERT INTO meta VALUES('revision','1') ON CONFLICT(key) DO UPDATE SET value=CAST(CAST(value AS INTEGER)+1 AS TEXT)")

    def bootstrap(self, code, password):
        if not code or len(password) < 8 or password == DEFAULT_PASSWORD:
            raise ValueError('Define un código de supervisor y una clave de al menos 8 caracteres.')
        with self.lock, self.db:
            if self.db.execute('SELECT 1 FROM users').fetchone():
                return
            self.db.execute('INSERT INTO users VALUES(?,?,?,?,1,NULL)', (code, 'Supervisor', password_hash(password), 'supervisor'))
            self._audit(code, 'INICIO', 'Supervisor inicial creado')

    def login(self, code, password):
        with self.lock:
            row = self.db.execute('SELECT * FROM users WHERE code=?', (text(code),)).fetchone()
            if not row or not matches(password, row['password']):
                raise ValueError('Código o clave incorrectos.')
            return self.public_user(row)

    @staticmethod
    def public_user(row):
        return {k: row[k] for k in ('code', 'name', 'role', 'must_change', 'laser_id')}

    def user(self, code):
        with self.lock:
            row = self.db.execute('SELECT * FROM users WHERE code=?', (code,)).fetchone()
            if not row:
                raise ValueError('Usuario inexistente.')
            return self.public_user(row)

    def change_password(self, code, old, new):
        if len(new) < 8 or new == DEFAULT_PASSWORD or new == old:
            raise ValueError('Usa una clave nueva de al menos 8 caracteres.')
        with self.lock, self.db:
            self.login(code, old)
            self.db.execute('UPDATE users SET password=?, must_change=0 WHERE code=?', (password_hash(new), code))
            self._audit(code, 'CLAVE', 'Cambio de contraseña')

    def supervisor(self, code):
        user = self.user(code)
        if user['must_change']:
            raise ValueError('Primero debes cambiar tu clave.')
        if user['role'] != 'supervisor':
            raise ValueError('Solo el supervisor puede realizar esta operación.')

    def add_employee(self, actor, code, name):
        with self.lock, self.db:
            self.supervisor(actor)
            code, name = text(code), text(name)
            if not code or not name:
                raise ValueError('Completa código y nombre.')
            self.db.execute('INSERT INTO users VALUES(?,?,?,?,1,NULL)', (code, name, password_hash(DEFAULT_PASSWORD), 'operator'))
            self._audit(actor, 'EMPLEADO', code)

    def save_laser(self, actor, name, account='', laser_id=None):
        with self.lock, self.db:
            self.supervisor(actor)
            name = text(name)
            if not name or len(name) > 60:
                raise ValueError('Nombre de láser requerido (máximo 60 caracteres).')
            if laser_id is None:
                cur = self.db.execute('INSERT INTO lasers(name,cpa_account) VALUES(?,?)', (name, text(account)))
                lid = cur.lastrowid
                self.db.execute('UPDATE lasers SET external_id=?,sheet_title=?,sort_order=? WHERE id=?', (f'LASER_{lid:02d}', f'Láser {lid:02d}', lid, lid))
            else:
                if not self.db.execute('SELECT 1 FROM lasers WHERE id=?', (laser_id,)).fetchone():
                    raise ValueError('Láser inexistente.')
                self.db.execute('UPDATE lasers SET name=?,cpa_account=? WHERE id=?', (name, text(account), laser_id))
            self.db.execute("INSERT INTO meta VALUES('config_dirty','1') ON CONFLICT(key) DO UPDATE SET value='1'")
            self._audit(actor, 'LASER', name)

    def set_laser_active(self, actor, laser_id, active):
        with self.lock, self.db:
            self.supervisor(actor)
            row = self.db.execute('SELECT name FROM lasers WHERE id=?', (laser_id,)).fetchone()
            if not row:
                raise ValueError('Láser inexistente.')
            self.db.execute('UPDATE lasers SET active=? WHERE id=?', (int(bool(active)), laser_id))
            if not active:
                self.db.execute('UPDATE users SET laser_id=NULL WHERE laser_id=?', (laser_id,))
            self.db.execute("INSERT INTO meta VALUES('config_dirty','1') ON CONFLICT(key) DO UPDATE SET value='1'")
            self._audit(actor, 'LASER_ACTIVA', f'{laser_id}: {bool(active)}')

    def import_directory(self, employees, lasers):
        """Entradas de Sheets; nunca sobrescribe claves, roles ni historial."""
        with self.lock, self.db:
            changed = 0
            keys = {r['external_id'] for r in lasers}
            for item in lasers:
                old = self.db.execute('SELECT * FROM lasers WHERE external_id=?', (item['external_id'],)).fetchone()
                values = (item['name'], item['sheet_title'], item['active'], item['sort_order'])
                if old:
                    if tuple(old[k] for k in ('name', 'sheet_title', 'active', 'sort_order')) != values:
                        self.db.execute('UPDATE lasers SET name=?,sheet_title=?,active=?,sort_order=? WHERE id=?', values + (old['id'],))
                        changed += 1
                    lid = old['id']
                else:
                    lid = self.db.execute('INSERT INTO lasers(name,sheet_title,active,sort_order,external_id) VALUES(?,?,?,?,?)', values + (item['external_id'],)).lastrowid
                    changed += 1
                if not item['active']:
                    self.db.execute('UPDATE users SET laser_id=NULL WHERE laser_id=?', (lid,))
            # Quitar del catálogo significa desactivar, nunca borrar producción.
            for row in self.db.execute('SELECT id,external_id FROM lasers WHERE active=1').fetchall():
                if row['external_id'] not in keys:
                    self.db.execute('UPDATE lasers SET active=0 WHERE id=?', (row['id'],))
                    self.db.execute('UPDATE users SET laser_id=NULL WHERE laser_id=?', (row['id'],))
                    changed += 1
            for code, name in employees:
                old = self.db.execute('SELECT name,role FROM users WHERE code=?', (code,)).fetchone()
                if old is None:
                    self.db.execute('INSERT INTO users VALUES(?,?,?,?,1,NULL)', (code, name, password_hash(DEFAULT_PASSWORD), 'operator'))
                    changed += 1
                elif old['role'] == 'operator' and old['name'] != name:
                    self.db.execute('UPDATE users SET name=? WHERE code=?', (name, code))
                    changed += 1
            if changed:
                self._audit('sistema', 'DIRECTORIO_SHEETS', f'{changed} cambios')
            return changed

    def assign(self, actor, code, laser_id):
        with self.lock, self.db:
            self.supervisor(actor)
            if laser_id is not None and not self.db.execute('SELECT 1 FROM lasers WHERE id=? AND active=1', (laser_id,)).fetchone():
                raise ValueError('Láser inexistente o desactivada.')
            row = self.db.execute('SELECT role FROM users WHERE code=?', (code,)).fetchone()
            if not row or row['role'] != 'operator':
                raise ValueError('Selecciona un operador válido.')
            self.db.execute('UPDATE users SET laser_id=? WHERE code=?', (laser_id, code))
            self._audit(actor, 'ASIGNACION', f'{code} -> {laser_id}')

    def import_cpa(self, actor, rows):
        data = normalize_cpa(rows)
        with self.lock, self.db:
            self.supervisor(actor)
            self.db.execute('DELETE FROM cpa')
            self.db.executemany('INSERT INTO cpa VALUES(?,?,?)', data)
            self.db.execute("INSERT INTO meta VALUES('cpa_updated',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (stamp(),))
            self._audit(actor, 'CPA', f'{len(data)} líneas importadas')
        return len(data)

    def report(self, actor, request_id, order, line, qty, comment='', reprint=False, laser_id=None, report_date=None):
        order, line = identifier(order), identifier(line).upper()
        qty = pieces(qty)
        report_date = text(report_date) or stamp()[:10]
        try:
            date.fromisoformat(report_date)
        except ValueError:
            raise ValueError('Fecha inválida. Usa AAAA-MM-DD.') from None
        if not order or not line or qty <= 0 or not request_id:
            raise ValueError('Completa orden, línea y una cantidad de piezas mayor a cero.')
        if len(order) > 60 or len(line) > 20 or len(comment) > 2000:
            raise ValueError('Los datos superan el tamaño permitido.')
        with self.lock, self.db:
            user = self.user(actor)
            if user['must_change']:
                raise ValueError('Primero debes cambiar tu clave.')
            if user['role'] != 'supervisor':
                laser_id = user['laser_id']  # Nunca confiar en la láser enviada por el cliente.
            if laser_id is None:
                raise ValueError('Esperando a ser asignado. Habla con tu supervisor.')
            if not self.db.execute('SELECT 1 FROM lasers WHERE id=? AND active=1', (laser_id,)).fetchone():
                raise ValueError('Selecciona una láser activa válida.')
            existing = self.db.execute('SELECT id,employee FROM reports WHERE request_id=?', (request_id,)).fetchone()
            if existing:
                if existing['employee'] != actor:
                    raise ValueError('Identificador de envío inválido.')
                return existing['id']
            cur = self.db.execute('INSERT INTO reports(request_id,created,employee,laser_id,ord,line,qty,comment,reprint) VALUES(?,?,?,?,?,?,?,?,?)', (request_id, stamp(), actor, laser_id, order, line, qty, text(comment), int(bool(reprint))))
            self.db.execute('UPDATE reports SET report_date=? WHERE id=?', (report_date, cur.lastrowid))
            self._audit(actor, 'REPORTE', f'{cur.lastrowid}: {order}|{line} ({qty})')
            return cur.lastrowid

    def snapshot(self, actor):
        with self.lock:
            user = self.user(actor)
            if user['must_change']:
                raise ValueError('Primero debes cambiar tu clave.')
            all_reports = [dict(r) for r in self.db.execute('SELECT r.*,l.name AS laser FROM reports r JOIN lasers l ON l.id=r.laser_id ORDER BY r.id DESC')]
            cpa = {(r['ord'], r['line']): r['qty'] for r in self.db.execute('SELECT * FROM cpa')}
            grouped = {}
            for row in all_reports:
                grouped.setdefault((row['ord'], row['line']), []).append(row)
            for row in all_reports:
                row['report_date'] = row.get('report_date') or row['created'][:10]
                key = row['ord'], row['line']
                group = grouped[key]
                messages = []
                row['color'] = ''
                row['duplicate'] = len(group) > 1
                row['excess'] = key in cpa and sum(r['qty'] for r in group) > cpa[key]
                row['missing'] = key not in cpa
                if row['duplicate']:
                    messages.append('Duplicada: ' + ', '.join(sorted({r['laser'] for r in group})))
                if row['excess']:
                    messages.append(f"Orden adulterada · Acumulado {sum(r['qty'] for r in group)} / CPA {cpa[key]}")
                if row['missing']:
                    messages.append('La orden no existe en el CPA. Revisa el # de orden y la línea.')
                if row['duplicate'] or row['excess']:
                    row['color'] = 'red'
                elif row['missing']:
                    row['color'] = 'yellow'
                row['alert'] = ' | '.join(messages)
            is_supervisor = user['role'] == 'supervisor'
            visible = all_reports if is_supervisor else [r for r in all_reports if r['laser_id'] == user['laser_id']]
            lasers = [dict(r) for r in self.db.execute('SELECT * FROM lasers ORDER BY sort_order,id')]
            if not is_supervisor:
                lasers = [{k: r[k] for k in ('id', 'name')} for r in lasers if r['id'] == user['laser_id'] and r['active']]
            meta = dict(self.db.execute('SELECT key,value FROM meta'))
            return {'user': user, 'reports': visible, 'lasers': lasers,
                    'employees': [self.public_user(r) for r in self.db.execute("SELECT * FROM users WHERE role='operator' ORDER BY code")] if is_supervisor else [],
                    'today': stamp()[:10], 'cpa_lines': len(cpa),
                    'cpa_updated': meta.get('cpa_updated', 'Sin cargar'),
                    'sync': {k: meta.get(k, '') for k in ('dirty', 'sync_at', 'sync_error')}}
