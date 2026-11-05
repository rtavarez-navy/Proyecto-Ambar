import unittest
from corte_sk2.core import Store
from corte_sk2.sheets import SheetsSync, CONFIG_HEADERS, parse_directory


class Worksheet:
    def __init__(self, title, sid, values=None):
        self.title, self.id = title, sid
        self.row_count, self.col_count, self.frozen_row_count = 1000, 13, 1
        self.values = values or []

    def resize(self, rows, cols):
        self.row_count, self.col_count = rows, cols

    def freeze(self, rows):
        self.frozen_row_count = rows

    def get(self, range_name, value_render_option=None):
        return getattr(self, 'formulas', self.values[1:2])

    def get_all_values(self):
        return self.values


class Book:
    def __init__(self):
        self.tabs = {}
        self.requests = []
        self.add_worksheet('Maestro', 1000, 3).values = [['APELLIDOS_NOMBRES', 'CODIGO_EMPLEADO', 'DEPARTAMENTO'], ['Operador Uno', '001', 'CORTE']]
        self.add_worksheet('Configuración', 1000, 5).values = [CONFIG_HEADERS, ['LASER_01', 'Láser 01', 'Láser 01', 1, True]]

    def worksheets(self):
        return list(self.tabs.values())

    def add_worksheet(self, title, rows, cols):
        self.tabs[title] = Worksheet(title, len(self.tabs) + 1)
        return self.tabs[title]

    def batch_update(self, body):
        self.requests.extend(body['requests'])

    def values_batch_update(self, body):
        assert body['valueInputOption'] == 'RAW'
        for data in body['data']:
            name = data['range'].split('!')[0].strip("'").replace("''", "'")
            self.tabs[name].values = data['values']

    def worksheet(self, name):
        return self.tabs[name]


class SheetsTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.store.bootstrap('S', 'supervisor123')
        self.store.change_password('S', 'supervisor123', 'supervisor456')
        self.store.save_laser('S', 'Láser 01', 'cpa.user')
        self.store.import_cpa('S', [{'Order': '100', 'Set': 'A', 'Qty': 10}])
        self.store.report('S', 'one', '100', 'A', 12, comment='=no_es_formula', laser_id=1)
        self.book = Book()
        self.sync = SheetsSync(self.store, 'test', 'not-used')
        self.sync.book = lambda: self.book

    def tearDown(self):
        self.store.db.close()

    def test_sync_and_restore_complete_state(self):
        self.sync.sync()
        self.assertEqual(self.store.snapshot('S')['sync']['sync_error'], '')
        self.assertEqual(self.book.tabs['Consolidado'].values[1][4], '=no_es_formula')
        self.assertIn('Orden adulterada', self.book.tabs['Kyoodo'].values[1][3])
        self.assertNotIn('Laser 1', self.book.tabs)
        self.assertIn('Láser 01', self.book.tabs)
        formulas = [r['updateCells']['rows'][1]['values'][0]['userEnteredValue']['formulaValue'] for r in self.book.requests if 'updateCells' in r and len(r['updateCells'].get('rows', [])) == 2]
        self.assertIn('Consolidado!A2:M', formulas[0])
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
        original = self.book.batch_update
        def callback(data):
            nonlocal did_change
            if not did_change:
                did_change = True
                self.store.report('S', 'two', '100', 'A', 1, laser_id=1)
            original(data)
        self.book.batch_update = callback
        self.sync.sync()
        self.assertEqual(self.store.snapshot('S')['sync']['dirty'], '1')
        self.sync.sync()
        self.assertEqual(self.store.snapshot('S')['sync']['sync_error'], '')
        self.assertEqual(self.store.snapshot('S')['sync']['dirty'], '0')
        self.assertEqual(len(self.book.tabs['Consolidado'].values), 3)

    def test_master_refresh_preserves_password_and_assignment(self):
        self.sync.sync()
        self.store.change_password('001', '123456', 'personal123')
        self.store.assign('S', '001', 1)
        self.book.tabs['Maestro'].values[1][0] = 'Nombre corregido'
        self.sync.sync()
        user = self.store.login('001', 'personal123')
        self.assertEqual(user['name'], 'Nombre corregido')
        self.assertEqual(user['laser_id'], 1)
        self.assertEqual(user['must_change'], 0)

    def test_remote_config_read_even_when_no_reports_dirty(self):
        self.sync.sync()
        self.book.tabs['Configuración'].values[1][4] = False
        self.sync.sync()
        self.assertEqual(self.store.snapshot('S')['lasers'][0]['active'], 0)
        with self.assertRaises(ValueError):
            self.store.report('S', 'new', '100', 'A', 1, laser_id=1)

    def test_unknown_remote_production_is_not_overwritten(self):
        original = [['FECHA'] * 10, ['2026-11-05', '100', 'A', 1, '', '', '', '', '', 'external']]
        self.book.add_worksheet('Consolidado', 1000, 13).values = original
        self.sync.sync()
        self.assertEqual(self.book.tabs['Consolidado'].values, original)
        self.assertIn('no se sobrescribió', self.store.snapshot('S')['sync']['sync_error'])

    def test_invalid_directory_does_not_mutate(self):
        self.store.db.execute("UPDATE meta SET value='0' WHERE key='config_dirty'")
        self.store.db.commit()
        self.book.tabs['Configuración'].values[1][2] = 'Consolidado'
        self.sync.sync()
        self.assertEqual(len(self.store.snapshot('S')['employees']), 0)
        with self.assertRaises(ValueError):
            parse_directory([['CODIGO_EMPLEADO', 'APELLIDOS_NOMBRES'], ['001', 'Uno'], ['001', 'Otro']], [CONFIG_HEADERS])

    def test_deactivation_preserves_history_and_unassigns(self):
        self.sync.sync()
        self.store.change_password('001', '123456', 'personal123')
        self.store.assign('S', '001', 1)
        self.store.set_laser_active('S', 1, False)
        self.assertEqual(self.store.user('001')['laser_id'], None)
        self.assertEqual(len(self.store.snapshot('S')['reports']), 1)
        self.sync.sync()
        self.assertEqual(self.book.tabs['Configuración'].values[1][4], False)

    def test_account_reference_can_be_edited_in_sheet(self):
        self.sync.sync()
        self.book.tabs['Cuentas CPA'].values[1][2] = 'new.cpa.user'
        self.sync.sync()
        self.assertEqual(self.store.snapshot('S')['lasers'][0]['cpa_account'], 'new.cpa.user')

    def test_credentials_round_trip_and_local_edit_preserves_password(self):
        self.sync.sync()
        self.book.tabs['Cuentas CPA'].values[1][2:] = ['operator@example.test', ' 001$ecret ']
        self.sync.sync()
        laser = self.store.snapshot('S')['lasers'][0]
        self.assertEqual(laser['cpa_password'], ' 001$ecret ')
        self.assertEqual(laser['cpa_account'], 'operator@example.test')
        self.store.save_laser('S', 'Láser 01 renombrada', laser['cpa_account'], 1)
        self.sync.sync()
        self.assertEqual(self.book.tabs['Cuentas CPA'].values[1][3], ' 001$ecret ')
        self.store.save_laser('S', 'Láser 01 renombrada', 'new@example.test', 1, 'new.secret')
        self.sync.sync()
        self.assertEqual(self.book.tabs['Cuentas CPA'].values[1][2:], ['new@example.test', 'new.secret'])

    def test_cpa_id_export_and_import_formula_preservation(self):
        self.store.import_cpa('S', [{'Order': '100', 'Set': 'A', 'Qty': 10, 'ID': '000123'}])
        self.sync.sync()
        self.assertEqual(self.book.tabs['CPA DATA'].values[0][3], 'ID')
        self.assertEqual(self.book.tabs['CPA DATA'].values[1][3], '000123')
        sheet = self.book.tabs['CPA DATA']
        sheet.formulas = [['=IMPORTRANGE("source","A2:A")']]
        sheet.values = [['Order', 'Set', 'Prod Line Pieces #', 'ID'], ['200', 'B', 30, '00999']]
        self.sync.sync()
        self.assertEqual(self.store.snapshot('S')['sync']['sync_error'], '')
        self.assertEqual(tuple(self.store.db.execute('SELECT ord,line,qty,source_id FROM cpa').fetchone()), ('200', 'B', 30, '00999'))
        self.assertTrue(sheet.formulas[0][0].startswith('=IMPORTRANGE'))
        self.assertEqual(sheet.values[1][0], '200')

    def test_blank_config_checkboxes_not_lasers(self):
        rows = [CONFIG_HEADERS, ['L1', 'Uno', 'Uno', 1, True], ['', '', '', '', False]]
        _, lasers = parse_directory([['APELLIDOS_NOMBRES', 'CODIGO_EMPLEADO']], rows)
        self.assertEqual(len(lasers), 1)

