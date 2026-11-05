"""Cola persistente. El bot solo modifica COMENTARIO y ESTADO por ID_REGISTRO."""
from .core import stamp, text

PENDING = 'PENDIENTE'
RETRY = 'REINTENTAR'
DONE = 'ACTUALIZADA A 910'
ALREADY = 'YA EN 910'
HIGHER = 'SUPERIOR A 910'
TERMINAL = (DONE, ALREADY, HIGHER)
BLOCKED = 'BLOQUEADA SIN ID CPA'
MISSING_ID = 'Falta el ID original para esta orden y línea en CPA DATA.'
STATES = (PENDING, RETRY, DONE, ALREADY, HIGHER, BLOCKED)
QUEUE_HEADERS = ['# DE ORDEN', 'LASER', 'SECUENCIA', 'COMENTARIO', 'LINEA', 'ID_REGISTRO', 'ID_CPA', 'ESTADO', 'ORDEN_LINEA', 'FECHA_REGISTRO', 'STATUS_DESTINO', 'LASER_ID']


class KyoodoQueue:
    def __init__(self, store):
        self.store = store
        with store.lock, store.db:
            store.db.execute('''CREATE TABLE IF NOT EXISTS kyoodo_jobs (
                request_id TEXT PRIMARY KEY REFERENCES reports(request_id),
                sequence INTEGER UNIQUE NOT NULL,
                created TEXT NOT NULL,
                ord TEXT NOT NULL,
                line TEXT NOT NULL,
                laser_name TEXT NOT NULL,
                laser_key TEXT NOT NULL,
                cpa_id TEXT NOT NULL,
                state TEXT NOT NULL,
                comment TEXT NOT NULL DEFAULT '',
                updated TEXT NOT NULL
            )''')
            self.ensure_jobs()

    def ensure_jobs(self):
        """Se llama dentro de la transacción del reporte; también migra reportes anteriores."""
        s = self.store
        cursor = s.db.execute('''INSERT OR IGNORE INTO kyoodo_jobs
            (request_id,sequence,created,ord,line,laser_name,laser_key,cpa_id,state,comment,updated)
            SELECT r.request_id,r.id,r.created,r.ord,r.line,l.name,l.external_id,
                COALESCE(c.source_id,''),
                CASE WHEN COALESCE(c.source_id,'')='' THEN ? ELSE ? END,
                CASE WHEN COALESCE(c.source_id,'')='' THEN ? ELSE '' END,?
            FROM reports r JOIN lasers l ON l.id=r.laser_id
            LEFT JOIN cpa c ON c.ord=r.ord AND c.line=r.line
            WHERE NOT EXISTS (SELECT 1 FROM kyoodo_jobs q WHERE q.request_id=r.request_id)''', (BLOCKED, PENDING, MISSING_ID, stamp()))
        if cursor.rowcount:
            s._audit('sistema', 'KYOODO_ENCOLAR', f'{cursor.rowcount} reportes')

    def resolve_missing_ids(self):
        """Solo resuelve trabajos bloqueados. Nunca reemplaza un ID ya asignado."""
        s = self.store
        with s.lock, s.db:
            rows = s.db.execute('''SELECT q.request_id,c.source_id FROM kyoodo_jobs q
                JOIN cpa c ON c.ord=q.ord AND c.line=q.line
                WHERE q.state=? AND q.cpa_id='' AND c.source_id!='' ''', (BLOCKED,)).fetchall()
            for row in rows:
                s.db.execute('UPDATE kyoodo_jobs SET cpa_id=?,state=?,comment=?,updated=? WHERE request_id=?',
                             (row['source_id'], PENDING, '', stamp(), row['request_id']))
                s._audit('sistema', 'KYOODO_ID_RESUELTO', row['request_id'])

    def jobs(self):
        with self.store.lock:
            return [dict(r) for r in self.store.db.execute('SELECT * FROM kyoodo_jobs ORDER BY sequence')]

    def merge_results(self, remote):
        """Conserva los resultados del bot y hace irreversible una confirmación a 910."""
        s = self.store
        with s.lock, s.db:
            for key, item in remote.items():
                local = s.db.execute('SELECT * FROM kyoodo_jobs WHERE request_id=?', (key,)).fetchone()
                if not local:
                    raise ValueError('Kyoodo contiene un ID_REGISTRO ajeno a esta base. Recupera el respaldo; no se modificó la cola.')
                row = item['values']
                if str(row[0]) != local['ord'] or str(row[4]) != local['line']:
                    raise ValueError('Kyoodo: orden o línea modificadas. Solo el bot debe editar COMENTARIO y ESTADO.')
                remote_id, state = text(row[6]), text(row[7])
                if remote_id and remote_id != local['cpa_id']:
                    raise ValueError('Kyoodo: el ID CPA no coincide con el trabajo original.')
                if not state:  # Lista anterior de seis columnas, sin estados.
                    if row[3] and str(row[3]) != local['comment']:
                        s.db.execute('UPDATE kyoodo_jobs SET comment=?,updated=? WHERE request_id=?', (str(row[3]), stamp(), key))
                        s._audit('sistema', 'KYOODO_MIGRAR_COMENTARIO', key)
                    continue
                if state not in STATES:
                    raise ValueError('Kyoodo contiene un ESTADO no reconocido.')
                if state in (PENDING, RETRY, *TERMINAL) and not remote_id:
                    raise ValueError('Kyoodo: no puede procesarse o confirmarse una fila sin ID_CPA.')
                if local['state'] in TERMINAL:
                    if state == local['state'] and str(row[3]) != local['comment']:
                        s.db.execute('UPDATE kyoodo_jobs SET comment=?,updated=? WHERE request_id=?', (str(row[3]), stamp(), key))
                        s._audit('kyoodo', 'KYOODO_COMENTARIO', key)
                    continue  # Nunca volver a encolar un trabajo confirmado.
                if state == BLOCKED and local['cpa_id']:
                    continue  # Será habilitado al escribir ID_CPA y PENDIENTE juntos.
                comment = str(row[3])
                if state != local['state'] or comment != local['comment']:
                    s.db.execute('UPDATE kyoodo_jobs SET state=?,comment=?,updated=? WHERE request_id=?', (state, comment, stamp(), key))
                    # El comentario podría incluir datos sensibles del CPA; no duplicarlo en auditoría.
                    s._audit('kyoodo', 'KYOODO_RESULTADO', f'{key}: {state}')


def read_remote_queue(ws):
    values = ws.get_all_values()
    if values and values[0] != QUEUE_HEADERS and values[0] != QUEUE_HEADERS[:6]:
        raise ValueError('Kyoodo: cabecera no compatible. No se sobrescribió la hoja.')
    remote = {}
    for position, original in enumerate(values[1:], 2):
        if not any(text(v) for v in original):
            continue
        row = list(original) + [''] * len(QUEUE_HEADERS)
        key = text(row[5])
        if not key or key in remote:
            raise ValueError('Kyoodo requiere un ID_REGISTRO único en cada fila.')
        remote[key] = {'row': position, 'values': row[:len(QUEUE_HEADERS)]}
    return remote, bool(values and values[0] == QUEUE_HEADERS)


def queue_values(job):
    return [job['ord'], job['laser_name'], job['sequence'], job['comment'], job['line'], job['request_id'],
            job['cpa_id'], job['state'], job['ord'] + '|' + job['line'], job['created'], 910, job['laser_key']]


def needs_queue_push(remote, current_header, jobs):
    if not current_header:
        return True
    for job in jobs:
        entry = remote.get(job['request_id'])
        if not entry:
            return True
        row = entry['values']
        if not row[7] or (row[7] == BLOCKED and job['cpa_id']) or (job['state'] in TERMINAL and row[7] != job['state']):
            return True
    return False


def push_queue(book, ws, remote, current_header, jobs):
    """Append-only: no ordena ni reescribe las columnas del bot en filas activas."""
    if ws.col_count < len(QUEUE_HEADERS):
        ws.resize(rows=ws.row_count, cols=len(QUEUE_HEADERS))
    changes = []
    if not current_header:
        changes.append({'range': "'Kyoodo'!A1", 'values': [QUEUE_HEADERS]})
    appended = []
    for job in jobs:
        entry = remote.get(job['request_id'])
        if not entry:
            appended.append(queue_values(job))
            continue
        position, row = entry['row'], entry['values']
        if not row[7]:  # Migración de la lista antigua, conservando su comentario.
            changes += [
                {'range': f"'Kyoodo'!C{position}", 'values': [[job['sequence']]]},
                {'range': f"'Kyoodo'!G{position}:L{position}", 'values': [[job['cpa_id'], job['state'], job['ord'] + '|' + job['line'], job['created'], 910, job['laser_key']]]},
            ]
        elif row[7] == BLOCKED and job['cpa_id']:
            changes.append({'range': f"'Kyoodo'!G{position}:H{position}", 'values': [[job['cpa_id'], job['state']]]})
            if row[3] == MISSING_ID:
                changes.append({'range': f"'Kyoodo'!D{position}", 'values': [['']]})
        elif job['state'] in TERMINAL and row[7] != job['state']:
            changes.append({'range': f"'Kyoodo'!H{position}", 'values': [[job['state']]]})
    if changes:
        book.values_batch_update({'valueInputOption': 'RAW', 'data': changes})
    if appended:
        # Si la respuesta se pierde, el siguiente ciclo vuelve a leer IDs antes de reintentar.
        ws.append_rows(appended, value_input_option='RAW', insert_data_option='INSERT_ROWS', table_range='A:L')


def actionable_rows(values):
    """Contrato de lectura del bot: primero el más viejo, un intento por fila/ciclo."""
    if not values or values[0] != QUEUE_HEADERS:
        raise ValueError('Cabecera de Kyoodo no compatible.')
    jobs, seen = [], set()
    for row in values[1:]:
        if not any(text(v) for v in row):
            continue
        if len(row) < len(QUEUE_HEADERS):
            raise ValueError('Fila de Kyoodo incompleta.')
        item = dict(zip(QUEUE_HEADERS, row))
        key = text(item['ID_REGISTRO'])
        if not key or key in seen:
            raise ValueError('ID_REGISTRO ausente o duplicado.')
        seen.add(key)
        if item['ESTADO'] in (PENDING, RETRY) and text(item['ID_CPA']):
            if str(item['STATUS_DESTINO']) != '910':
                raise ValueError('Destino CPA inesperado.')
            jobs.append(item)
    return sorted(jobs, key=lambda item: int(item['SECUENCIA']))
