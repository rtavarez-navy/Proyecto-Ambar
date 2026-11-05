import copy
import tempfile
import unittest
from unittest.mock import Mock, patch
from corte_sk2.cpa_browser import CPABrowser, CPAError, profile_order, status_code
from corte_sk2.kyoodo_worker import LaserLock, SheetQueue, run_cycle
from corte_sk2.kyoodo import QUEUE_HEADERS, PENDING, RETRY, DONE, ALREADY, HIGHER, actionable_rows
from test_sheets import Book
import test_kyoodo


def row(key='one', laser='LASER_01', seq=1):
    return ['SO-3969484', 'Láser 01', str(seq), '', 'A', key, '000123', PENDING,
            'SO-3969484|A', '2026-11-05', '910', laser]


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.book = Book()
        self.ws = self.book.add_worksheet('Kyoodo', 1000, 12)
        self.ws.values = [QUEUE_HEADERS, row('new', seq=9), row('other', 'LASER_02', 0), row('old', seq=1)]
        accounts = self.book.add_worksheet('Cuentas CPA', 1000, 4)
        accounts.values = [['LASER_ID', 'LASER', 'CORREO', 'CONTRASEÑA'], ['LASER_01', 'Uno', 'one@example.com', ' secret ']]
        self.queue = SheetQueue(self.book, 'LASER_01')

    def test_only_own_laser_oldest_first_and_account(self):
        seen = []
        def factory():
            browser = Mock()
            browser.update.side_effect = lambda job, before_save: (before_save(), seen.append(job['ID_REGISTRO']), (DONE, 'Confirmado'))[-1]
            return browser
        run_cycle(self.queue, factory, lambda *a: None)
        self.assertEqual(seen, ['old', 'new'])
        self.assertEqual(self.ws.values[2][7], PENDING)
        self.assertEqual(self.queue.credentials(), ('one@example.com', ' secret '))

    def test_error_continues_and_never_leaks_password(self):
        browser = Mock()
        browser.update.side_effect = [RuntimeError('password= secret '), (DONE, 'confirmado')]
        run_cycle(self.queue, lambda: browser, lambda *a: None)
        self.assertEqual(self.ws.values[3][7], RETRY)
        self.assertNotIn('secret', self.ws.values[3][3])
        self.assertEqual(self.ws.values[1][7], DONE)

    def test_missing_account_does_not_open_cpa(self):
        self.book.tabs['Cuentas CPA'].values = self.book.tabs['Cuentas CPA'].values[:1]
        factory = Mock()
        run_cycle(self.queue, factory, lambda *a: None)
        factory.assert_not_called()
        self.assertEqual(self.ws.values[3][7], RETRY)

    def test_result_refinds_row_and_does_not_rewrite_other_columns(self):
        job = self.queue.jobs()[0]
        expected = copy.deepcopy(self.ws.values[3])
        self.ws.values[1], self.ws.values[3] = self.ws.values[3], self.ws.values[1]
        self.queue.result(job, HIGHER, 'Sin cambios: 1300')
        current = self.ws.values[1]
        for index in range(12):
            if index not in (3, 7):
                self.assertEqual(current[index], expected[index])
        self.assertEqual(current[7], HIGHER)
        self.assertFalse(any(j['ID_REGISTRO'] == job['ID_REGISTRO'] for j in self.queue.jobs()))

    def test_changed_identity_prevents_save_and_result(self):
        job = self.queue.jobs()[0]
        self.ws.values[3][6] = 'another-id'
        with self.assertRaises(CPAError):
            self.queue.guard(job)
        with self.assertRaises(CPAError):
            self.queue.result(job, DONE, 'No escribir')
        self.assertEqual(self.ws.values[3][7], PENDING)

    def test_lost_google_response_rechecks_terminal_before_retry(self):
        original = self.book.values_batch_update
        def lost(body):
            original(body)
            raise OSError('respuesta perdida')
        self.book.values_batch_update = lost
        browser = Mock()
        browser.update.return_value = (DONE, 'Confirmado tras recargar')
        run_cycle(self.queue, lambda: browser, lambda *a: None)
        self.assertEqual(self.ws.values[1][7], DONE)
        self.assertEqual(self.ws.values[3][7], DONE)
        self.assertEqual(self.queue.jobs(), [])

    def test_lock_excludes_same_laser_and_releases_after_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):
                with LaserLock(directory, 'LASER_01'):
                    with self.assertRaises(ValueError):
                        with LaserLock(directory, 'LASER_01'):
                            pass
                    with LaserLock(directory, 'LASER_02'):
                        pass
                    raise RuntimeError('crash')
            with LaserLock(directory, 'LASER_01'):
                pass


class BrowserTests(unittest.TestCase):
    def browser(self):
        b = CPABrowser.__new__(CPABrowser)
        b.driver = Mock()
        b.driver.current_url = 'https://cp.champrosports.com/Profile/SO-3969484'
        b.wait = Mock()
        b.wait.until.side_effect = lambda fn: fn(b.driver) or (_ for _ in ()).throw(CPAError('espera fallida'))
        b.config = {}
        return b

    def test_visible_codes_not_option_values_and_order_validation(self):
        self.assertEqual(status_code('0910 – QC After Cut'), 910)
        self.assertEqual(status_code('1050 – Ready to Embroidery'), 1050)
        self.assertEqual(status_code('060 - Cutting'), 60)
        self.assertEqual(profile_order('3969484'), 'SO-3969484')
        for invalid in ('SO-12/other', 'https://other.com', '../login', 'SO-12?x=1'):
            with self.assertRaises(CPAError):
                profile_order(invalid)

    def test_equal_or_higher_never_edits_or_saves(self):
        for code, expected in [(910, ALREADY), (1300, HIGHER), (1800, HIGHER)]:
            b = self.browser()
            b._read = Mock(return_value=(Mock(), Mock(), code, f'{code} - Status'))
            self.assertEqual(b.update(dict(zip(QUEUE_HEADERS, row())))[0], expected)
            b.driver.find_elements.assert_not_called()
            b.driver.refresh.assert_not_called()

    def test_disabled_controls_require_real_edit_button(self):
        b = self.browser()
        select = Mock(); select.is_enabled.return_value = False
        b._read = Mock(return_value=(Mock(), select, 900, '0900 - Cutting'))
        with self.assertRaisesRegex(CPAError, 'edit_button_selector'):
            b.update(dict(zip(QUEUE_HEADERS, row())))
        b.driver.refresh.assert_not_called()

    def test_save_reload_and_exact_confirmation_required(self):
        for persisted in (910, 900, 1300):
            b = self.browser()
            element, tr, save = Mock(), Mock(), Mock()
            element.is_enabled.return_value = True
            b._read = Mock(side_effect=[(tr, element, 900, '0900 - Cutting'), (tr, element, 900, '0900 - Cutting'), (tr, element, persisted, f'{persisted} - Status')])
            save.is_enabled.side_effect = [True, False]
            b._one = Mock(return_value=save)
            disabled = Mock(); disabled.is_enabled.return_value = False
            b._line = Mock(return_value=(tr, disabled))
            guard = Mock()
            with patch('selenium.webdriver.support.ui.Select') as selected:
                if persisted == 910:
                    self.assertEqual(b.update(dict(zip(QUEUE_HEADERS, row())), guard)[0], DONE)
                else:
                    with self.assertRaisesRegex(CPAError, 'no se confirmó'):
                        b.update(dict(zip(QUEUE_HEADERS, row())), guard)
                selected.return_value.select_by_value.assert_called_once_with('91')
            self.assertEqual(guard.call_count, 2)
            save.click.assert_called_once()
            b.driver.refresh.assert_called_once()

    def test_line_id_is_exact_unique_and_set_must_match(self):
        b = self.browser()
        job = dict(zip(QUEUE_HEADERS, row()))
        def tr(cpa_id, line):
            result = Mock()
            cells = [Mock(), Mock()]; cells[0].text, cells[1].text = cpa_id, line
            select, option = Mock(), Mock()
            option.text = '0910 – QC After Cut'; option.get_attribute.return_value = '91'
            select.find_elements.return_value = [option]
            result.find_elements.side_effect = lambda by, selector: cells if selector == './td' else [select]
            return result
        b.driver.find_elements.return_value = [tr('123', 'A')]
        self.assertFalse(b._line(job))
        b.driver.find_elements.return_value = [tr('000123', 'B')]
        with self.assertRaisesRegex(CPAError, 'línea solicitada'):
            b._line(job)
        b.driver.find_elements.return_value = [tr('000123', 'A'), tr('000123', 'A')]
        with self.assertRaisesRegex(CPAError, 'duplicado'):
            b._line(job)
        b.driver.find_elements.return_value = [tr('000123', 'A')]
        self.assertTrue(b._line(job))


class TerminalSyncTests(unittest.TestCase):
    setUp = test_kyoodo.QueueTests.setUp
    tearDown = test_kyoodo.QueueTests.tearDown
    report = test_kyoodo.QueueTests.report
    sync_ok = test_kyoodo.QueueTests.sync_ok
    def test_terminal_skip_states_survive_sync_and_cannot_be_requeued(self):
        for state in (ALREADY, HIGHER):
            key = state
            self.report(key)
            self.sync_ok()
            target = next(r for r in self.book.tabs['Kyoodo'].values[1:] if r[5] == key)
            target[7], target[3] = state, 'Sin cambios en CPA'
            self.sync_ok()
            target[7] = PENDING
            self.sync_ok()
            self.assertEqual(target[7], state)
            self.assertFalse(any(j['ID_REGISTRO'] == key for j in actionable_rows(self.book.tabs['Kyoodo'].values)))
