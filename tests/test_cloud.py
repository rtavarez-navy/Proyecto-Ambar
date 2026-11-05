import copy
import threading
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from corte_sk2.cloud import CloudService
from corte_sk2.cloud_store import SheetsDatabase, PROCESSING
from corte_sk2.core import password_hash
from corte_sk2.kyoodo import DONE, RETRY
from test_sheets import Book


class CloudBook(Book):
    def __init__(self):
        super().__init__()
        self.commits = []
        self.add_worksheet('CPA DATA', 1000, 4).values = [['Order', 'Set', 'Prod Line Pieces #', 'ID'], ['900', 'A', 20, '000123']]
        self.add_worksheet('Láser 01', 1000, 13)

    def values_batch_get(self, ranges, params):
        return {'valueRanges': [{'values': copy.deepcopy(self.tabs[r.split('!')[0].strip("'")].values)} for r in ranges]}

    def values_batch_update(self, body):
        self.commits.append(copy.deepcopy(body))
        super().values_batch_update(body)


class Mutex:
    def __init__(self):
        self.lock = threading.RLock()

    @contextmanager
    def acquire(self):
        with self.lock:
            yield


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.book = CloudBook()
        self.mutex = Mutex()
        self.service = self.new_service()
        self.admin = self.call('/login', code='S', password='initial123')['token']
        self.call('/password', self.admin, old='initial123', new='changed123')
        self.call('/assign', self.admin, code='001', laser_id='LASER_01')
        self.worker = self.call('/login', code='001', password='123456')['token']
        self.call('/password', self.worker, old='123456', new='operator123')

    def new_service(self):
        return CloudService(SheetsDatabase(self.book), self.mutex, 'S', password_hash('initial123'))

    def call(self, path, token='', **data):
        return self.service.call(path, data, token)

    def report(self, key='one', token=None, **extra):
        data = {'request_id': key, 'order': '900', 'line': 'A', 'qty': 5, 'report_date': '2027-01-02', **extra}
        return self.service.call('/report', data, token or self.worker)

    def test_report_and_queue_are_one_raw_commit_and_retry_is_idempotent(self):
        self.report()
        batch = self.book.commits[-1]
        self.assertEqual(batch['valueInputOption'], 'RAW')
        self.assertTrue(any(r['range'].startswith("'Consolidado'!") for r in batch['data']))
        self.assertTrue(any(r['range'].startswith("'Kyoodo'!") for r in batch['data']))
        self.assertEqual(self.book.tabs['Kyoodo'].values[1][6], '000123')
        self.report()
        self.assertEqual(len(self.book.tabs['Consolidado'].values), 2)
        self.assertEqual(len(self.book.tabs['Kyoodo'].values), 2)

    def test_state_and_session_survive_service_restart(self):
        self.report()
        self.service = self.new_service()
        result = self.call('/snapshot', self.worker)
        self.assertEqual(result['backend'], 'google-sheets')
        self.assertEqual(result['reports'][0]['id'], 'one')
        self.assertEqual(result['user']['laser_id'], 'LASER_01')
        self.assertNotIn('cpa_password', result['lasers'][0])

    def test_first_password_change_and_unassigned_flow(self):
        self.call('/employee', self.admin, code='002', name='Otro')
        token = self.call('/login', code='002', password='123456')['token']
        with self.assertRaises(ValueError):
            self.call('/snapshot', token)
        self.call('/password', token, old='123456', new='another123')
        result = self.call('/snapshot', token)
        self.assertIsNone(result['user']['laser_id'])
        with self.assertRaises(ValueError):
            self.report('other', token=token, laser_id='LASER_01')

    def test_two_services_serialize_concurrent_reports_without_lost_rows(self):
        other = self.new_service()
        # Inicializar la segunda revisión antes de las escrituras simultáneas.
        other.call('/snapshot', {}, self.admin)
        errors = []
        def write(service, key):
            try:
                service.call('/report', {'request_id': key, 'order': '900', 'line': 'A', 'qty': 1}, self.worker)
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=write, args=(self.service if n % 2 else other, str(n))) for n in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertFalse(errors)
        self.assertEqual(len(self.book.tabs['Consolidado'].values), 11)
        self.assertEqual([r[2] for r in self.book.tabs['Kyoodo'].values[1:]], list(range(1, 11)))

    def test_commit_failure_leaves_no_partial_report(self):
        original = self.book.values_batch_update
        self.book.values_batch_update = lambda body: (_ for _ in ()).throw(OSError('failed'))
        with self.assertRaises(OSError):
            self.report()
        self.book.values_batch_update = original
        self.assertEqual(len(self.book.tabs['Consolidado'].values), 1)
        self.assertEqual(len(self.book.tabs['Kyoodo'].values), 1)
        self.report()
        self.assertEqual(len(self.book.tabs['Kyoodo'].values), 2)

    def test_lost_response_after_commit_retries_without_duplicate(self):
        original = self.book.values_batch_update
        def lost(body):
            original(body)
            raise OSError('lost response')
        self.book.values_batch_update = lost
        with self.assertRaises(OSError):
            self.report()
        self.book.values_batch_update = original
        self.report()
        self.assertEqual(len(self.book.tabs['Consolidado'].values), 2)

    def test_credentials_and_claim_are_scoped_to_assigned_laser(self):
        self.call('/laser', self.admin, laser_id='LASER_01', name='Láser 01', account='one@example.test', cpa_password='secret1')
        self.call('/laser', self.admin, name='Láser 02', account='two@example.test', cpa_password='secret2')
        self.report(laser_id='LASER_02')  # El cliente no puede falsificar su láser.
        lease = self.call('/kyoodo/next', self.worker)
        self.assertEqual(lease['account']['email'], 'one@example.test')
        self.assertEqual(lease['job']['LASER_ID'], 'LASER_01')
        self.assertEqual(lease['job']['ESTADO'], PROCESSING)
        self.assertTrue(self.call('/kyoodo/next', self.worker)['busy'])
        self.call('/employee', self.admin, code='002', name='Otro')
        self.call('/assign', self.admin, code='002', laser_id='LASER_02')
        second = self.call('/login', code='002', password='123456')['token']
        self.call('/password', second, old='123456', new='another123')
        with self.assertRaises(PermissionError):
            self.call('/kyoodo/result', second, request_id='one', claim_id=lease['job']['CLAIM_ID'], state=DONE)
        self.call('/kyoodo/result', self.worker, request_id='one', claim_id=lease['job']['CLAIM_ID'], state=RETRY, comment='CPA sin respuesta')
        next_job = self.call('/kyoodo/next', self.worker)['job']
        self.assertEqual(next_job['COMENTARIO'], 'CPA sin respuesta')
        self.call('/kyoodo/result', self.worker, request_id='one', claim_id=next_job['CLAIM_ID'], state=DONE)
        self.report()
        self.assertIsNone(self.call('/kyoodo/next', self.worker)['job'])
        self.assertEqual(self.book.tabs['Kyoodo'].values[1][7], DONE)

    def test_expired_lease_can_be_retried_but_old_worker_cannot_confirm(self):
        self.call('/laser', self.admin, laser_id='LASER_01', name='Láser 01', account='one@example.test', cpa_password='secret1')
        self.report()
        first = self.call('/kyoodo/next', self.worker)['job']
        self.book.tabs['Kyoodo'].values[1][13] = 0
        second = self.call('/kyoodo/next', self.worker)['job']
        self.assertNotEqual(first['CLAIM_ID'], second['CLAIM_ID'])
        with self.assertRaises(PermissionError):
            self.call('/kyoodo/result', self.worker, request_id='one', claim_id=first['CLAIM_ID'], state=DONE)

    def test_failed_login_throttle_persists_across_restarts(self):
        for _ in range(10):
            with self.assertRaises(ValueError):
                self.call('/login', code='001', password='wrong')
        self.service = self.new_service()
        with self.assertRaisesRegex(ValueError, 'Demasiados'):
            self.call('/login', code='001', password='operator123')

    def test_password_change_revokes_other_sessions(self):
        old = self.call('/login', code='001', password='operator123')['token']
        self.call('/password', self.worker, old='operator123', new='new.operator')
        with self.assertRaises(PermissionError):
            self.call('/snapshot', old)
        self.call('/snapshot', self.worker)

    def test_cpa_formula_is_not_overwritten(self):
        self.book.tabs['CPA DATA'].formulas = [['=IMPORTRANGE("private","A:D")']]
        with self.assertRaisesRegex(ValueError, 'fórmulas'):
            self.call('/cpa', self.admin, rows=[{'Order': '100', 'Set': 'B', 'Qty': 1}])
        self.assertEqual(self.book.tabs['CPA DATA'].values[1][3], '000123')

    def test_retry_of_claim_request_returns_same_reservation(self):
        self.call('/laser', self.admin, laser_id='LASER_01', name='Láser 01', account='one@example.test', cpa_password='secret1')
        self.report()
        first = self.call('/kyoodo/next', self.worker, request_id='same-claim-request')
        second = self.call('/kyoodo/next', self.worker, request_id='same-claim-request')
        self.assertEqual(first['job']['CLAIM_ID'], second['job']['CLAIM_ID'])
        self.assertEqual(second['job']['INTENTOS'], 1)
        self.assertTrue(self.call('/kyoodo/next', self.worker, request_id='another-device')['busy'])

    def test_migration_preserves_legacy_supervisor_password(self):
        import json
        book = CloudBook()
        book.add_worksheet('_Estado', 1000, 2).values = [['Backup', 'JSON'], [0, json.dumps({'version': 2, 'tables': {'lasers': [{'id': 1, 'external_id': 'LASER_01'}], 'users': [{'code': 'OLD', 'name': 'Supervisor anterior', 'password': password_hash('old.password'), 'role': 'supervisor', 'must_change': 0, 'laser_id': None}]}})]]
        service = CloudService(SheetsDatabase(book), Mutex(), 'NEW', password_hash('new.password'))
        token = service.call('/login', {'code': 'OLD', 'password': 'old.password'})['token']
        self.assertEqual(service.call('/snapshot', {}, token)['user']['code'], 'OLD')
        with self.assertRaises(ValueError):
            service.call('/login', {'code': 'NEW', 'password': 'new.password'})

    def test_sheet_orders_with_so_prefix_match_numeric_operator_entry(self):
        self.book.tabs['CPA DATA'].values[1][0] = 'SO-900'
        self.report()
        row = self.book.tabs['Kyoodo'].values[1]
        self.assertEqual(row[6], '000123')
        report = self.call('/snapshot', self.worker)['reports'][0]
        self.assertFalse(report['missing'])

    def test_flask_endpoint_and_transport_error(self):
        from corte_sk2.cloud import create_app, BusyError
        client = create_app(self.service).test_client()
        self.assertEqual(client.get('/health').status_code, 200)
        self.assertEqual(client.post('/snapshot', json={}).status_code, 403)
        response = client.post('/snapshot', json={}, headers={'Authorization': 'Bearer ' + self.worker})
        self.assertEqual(response.json['backend'], 'google-sheets')
        with patch.object(self.service, 'call', side_effect=BusyError('busy')):
            self.assertEqual(client.post('/report', json={}).status_code, 503)

    def test_https_client_preserves_uncertain_errors(self):
        from corte_sk2.client import API, TransportError
        from urllib.error import HTTPError
        import io
        with self.assertRaises(ValueError):
            API('http://localhost:8765')
        with patch('corte_sk2.client.urlopen', side_effect=HTTPError('https://test', 503, 'busy', {}, io.BytesIO(b'{"error":"busy"}'))):
            with self.assertRaises(TransportError):
                API('https://test').call('/report', request_id='same')
        with patch('corte_sk2.client.urlopen') as opened:
            opened.return_value.__enter__.return_value = io.BytesIO(b'not-json')
            with self.assertRaises(TransportError):
                API('https://test').call('/report', request_id='same')


class StorageMutexTests(unittest.TestCase):
    def test_conditional_create_excludes_second_writer_and_generation_protects_release(self):
        from google.api_core.exceptions import PreconditionFailed
        from corte_sk2.cloud import StorageMutex, BusyError
        state = {'generation': 0, 'live': False}
        class Blob:
            def upload_from_string(self, data, **kwargs):
                assert kwargs['if_generation_match'] == 0
                if state['live']:
                    raise PreconditionFailed('exists')
                state['generation'] += 1
                state['live'] = True
                self.generation = state['generation']
            def delete(self, **kwargs):
                if kwargs['if_generation_match'] != state['generation']:
                    raise PreconditionFailed('generation changed')
                state['live'] = False
        class Bucket:
            def blob(self, name):
                return Blob()
        first, second = StorageMutex(Bucket(), 'sheet'), StorageMutex(Bucket(), 'sheet')
        with first.acquire():
            with self.assertRaises(BusyError):
                with second.acquire():
                    pass
            self.assertTrue(state['live'])
        self.assertFalse(state['live'])
        with self.assertRaises(PreconditionFailed):
            with first.acquire():
                state['generation'] += 1
        self.assertTrue(state['live'])  # Nunca elimina una generación ajena.
