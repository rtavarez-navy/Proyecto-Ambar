import tempfile
import threading
import unittest
from pathlib import Path
from corte_sk2.core import Store, normalize_cpa
from corte_sk2.server import App
from corte_sk2.importers import read_cpa


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.store.bootstrap('SUP', 'supervisor123')
        self.store.change_password('SUP', 'supervisor123', 'supervisor456')
        self.store.save_laser('SUP', 'Láser 1', 'cuenta1')
        self.store.save_laser('SUP', 'Láser 2', 'cuenta2')
        for code in ['100', '200']:
            self.store.add_employee('SUP', code, 'Operador ' + code)
            self.store.change_password(code, '123456', 'personal123')
        self.store.assign('SUP', '100', 1)
        self.store.assign('SUP', '200', 2)
        self.store.import_cpa('SUP', [{'Order': '3965656', 'Set': 'A', 'Prod Line Pieces #': 10}])

    def tearDown(self):
        self.store.db.close()

    def report(self, actor='100', key='r1', qty=5, order='3965656', line='A', **kwargs):
        return self.store.report(actor, key, order, line, qty, **kwargs)

    def test_duplicate_colors_both_lasers_and_cumulative_excess(self):
        self.report(qty=6)
        self.report('200', 'r2', 5)
        rows = self.store.snapshot('SUP')['reports']
        self.assertTrue(all(r['color'] == 'red' and r['duplicate'] and r['excess'] for r in rows))
        self.assertIn('Orden adulterada', rows[0]['alert'])
        self.assertIn('Láser 1', rows[0]['alert'])
        self.assertIn('Láser 2', rows[0]['alert'])

    def test_same_laser_duplicate_marks_previous_row(self):
        self.report(qty=2)
        self.report(key='r2', qty=2)
        self.assertTrue(all(r['duplicate'] and r['color'] == 'red' for r in self.store.snapshot('100')['reports']))

    def test_missing_order_yellow(self):
        self.report(order='000404')
        row = self.store.snapshot('100')['reports'][0]
        self.assertEqual(row['color'], 'yellow')
        self.assertIn('La orden no existe en el CPA', row['alert'])

    def test_distinct_sets_are_not_duplicates(self):
        self.store.import_cpa('SUP', [{'Order': '3965656', 'Set': 'A', 'Qty': 10}, {'Order': '3965656', 'Set': 'B', 'Qty': 10}])
        self.report()
        self.report(key='r2', line='B')
        self.assertFalse(any(r['duplicate'] for r in self.store.snapshot('SUP')['reports']))

    def test_operator_cannot_forge_laser_or_access_other_reports(self):
        self.report(laser_id=2)
        self.report('200', 'r2')
        rows = self.store.snapshot('100')['reports']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['laser_id'], 1)
        self.assertEqual(self.store.snapshot('100')['employees'], [])
        self.assertNotIn('cpa_account', self.store.snapshot('100')['lasers'][0])
        with self.assertRaises(ValueError):
            self.store.assign('100', '200', 1)

    def test_unassigned_and_password_change_enforced(self):
        self.store.assign('SUP', '100', None)
        with self.assertRaisesRegex(ValueError, 'Esperando'):
            self.report()
        self.store.add_employee('SUP', '300', 'Nuevo')
        with self.assertRaisesRegex(ValueError, 'cambiar tu clave'):
            self.store.snapshot('300')
        self.assertEqual(self.store.login('300', '123456')['must_change'], 1)

    def test_retry_idempotency_concurrent_submissions(self):
        errors = []
        def write():
            try:
                self.report()
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=write) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertFalse(errors)
        self.assertEqual(len(self.store.snapshot('SUP')['reports']), 1)

    def test_import_is_atomic_and_ignores_order_prod_line_index(self):
        with self.assertRaises(ValueError):
            self.store.import_cpa('SUP', [{'Order': '1', 'Set': 'A', 'Qty': 'bad'}])
        self.assertEqual(self.store.snapshot('SUP')['cpa_lines'], 1)
        self.assertEqual(normalize_cpa([{'Order': '1', 'Set': 'A', 'Prod Line Pieces #': 8, 'Qty': 50, 'Order Prod Lines #': 999}]), [('1', 'A', 8)])
        with self.assertRaises(ValueError):
            normalize_cpa([{'Order': '1', 'Set': 'A', 'Qty': 8}, {'Order': '1', 'Set': 'A', 'Qty': 9}])

    def test_reimport_recalculates_existing_alerts(self):
        self.report(qty=12)
        self.assertTrue(self.store.snapshot('100')['reports'][0]['excess'])
        self.store.import_cpa('SUP', [{'Order': '3965656', 'Set': 'A', 'Qty': 20}])
        self.assertFalse(self.store.snapshot('100')['reports'][0]['excess'])

    def test_service_authorization_and_password_invalidation(self):
        app = App(self.store)
        token = app.call('/login', {'code': '100', 'password': 'personal123'})['token']
        other = app.call('/login', {'code': '100', 'password': 'personal123'})['token']
        with self.assertRaises(ValueError):
            app.call('/laser', {'name': 'Intruso'}, token)
        with self.assertRaises(PermissionError):
            app.call('/snapshot', {}, 'forged')
        app.call('/password', {'old': 'personal123', 'new': 'changed123'}, token)
        with self.assertRaises(PermissionError):
            app.call('/snapshot', {}, other)
        app.call('/logout', {}, token)
        with self.assertRaises(PermissionError):
            app.call('/snapshot', {}, token)

    def test_login_throttle(self):
        app = App(self.store)
        for _ in range(10):
            with self.assertRaises(ValueError):
                app.call('/login', {'code': '100', 'password': 'wrong'}, ip='station')
        with self.assertRaisesRegex(ValueError, 'Demasiados'):
            app.call('/login', {'code': '100', 'password': 'personal123'}, ip='station')

    def test_persistence_and_csv_import(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.db'
            s = Store(path)
            s.bootstrap('01', 'initial123')
            s.db.close()
            s = Store(path)
            self.assertEqual(s.login('01', 'initial123')['code'], '01')
            s.db.close()
            csv = Path(folder) / 'cpa.csv'
            csv.write_text('Order;Set;Prod Line Pieces #\n001;A;20\n', encoding='utf-8')
            self.assertEqual(normalize_cpa(read_cpa(csv)), [('001', 'A', 20)])


if __name__ == '__main__':
    unittest.main()
