import unittest
from corte_sk2.core import Store
from corte_sk2.sheets import SheetsSync


class Worksheet:
    def __init__(self, title, sid):
        self.title, self.id = title, sid
        self.row_count, self.col_count, self.frozen_row_count = 100, 12, 0
        self.values = []

    def resize(self, rows, cols):
        self.row_count, self.col_count = rows, cols

    def update(self, values, range_name, value_input_option):
        assert value_input_option == 'RAW'
        self.values = values

    def freeze(self, rows):
        self.frozen_row_count = rows

    def get_all_values(self):
        return self.values


class Book:
    def __init__(self):
        self.tabs = {}

    def worksheets(self):
        return list(self.tabs.values())

    def add_worksheet(self, title, rows, cols):
        self.tabs[title] = Worksheet(title, len(self.tabs) + 1)
        return self.tabs[title]

    def batch_update(self, request):
        pass

    def worksheet(self, name):
        return self.tabs[name]


class SheetsTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.store.bootstrap('S', 'supervisor123')
        self.store.change_password('S', 'supervisor123', 'supervisor456')
        self.store.save_laser('S', 'Láser 1', 'cpa.user')
        self.store.import_cpa('S', [{'Order': '100', 'Set': 'A', 'Qty': 10}])
        self.store.report('S', 'one', '100', 'A', 12, comment='=no_es_formula', laser_id=1)
        self.book = Book()
        self.sync = SheetsSync(self.store, 'test', 'not-used')
        self.sync.book = lambda: self.book

    def tearDown(self):
        self.store.db.close()

    def test_sync_and_restore_complete_state(self):
        self.sync.sync()
        self.assertEqual(self.book.tabs['Laser 1'].values[1][4], '=no_es_formula')
        self.assertIn('Orden adulterada', self.book.tabs['Kyoodo'].values[1][3])
        self.assertEqual(self.store.snapshot('S')['sync']['dirty'], '0')
        restored = Store(':memory:')
        sync = SheetsSync(restored, 'test', 'not-used')
        sync.book = lambda: self.book
        sync.restore_empty()
        self.assertEqual(restored.login('S', 'supervisor456')['code'], 'S')
        self.assertEqual(restored.snapshot('S')['reports'][0]['qty'], 12)
        self.assertEqual(restored.snapshot('S')['lasers'][0]['cpa_account'], 'cpa.user')
        with self.assertRaises(ValueError):
            sync.restore_empty()
        restored.db.close()

    def test_failure_keeps_dirty_and_reports_error(self):
        def fail():
            raise OSError('private credential detail')
        self.sync.book = fail
        self.sync.sync()
        state = self.store.snapshot('S')['sync']
        self.assertEqual(state['dirty'], '1')
        self.assertIn('no se pudo sincronizar', state['sync_error'])
        self.assertNotIn('credential', state['sync_error'])

    def test_changes_during_sync_stay_pending(self):
        did_change = False
        def callback(data):
            nonlocal did_change
            if not did_change:
                did_change = True
                self.store.report('S', 'two', '100', 'A', 1, laser_id=1)
        self.book.batch_update = callback
        self.sync.sync()
        self.assertEqual(self.store.snapshot('S')['sync']['dirty'], '1')
        self.sync.sync()
        self.assertEqual(self.store.snapshot('S')['sync']['dirty'], '0')
        self.assertEqual(len(self.book.tabs['Laser 1'].values), 3)
