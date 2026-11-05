import json
import tempfile
import unittest
from pathlib import Path
from corte_sk2.core import Store
from corte_sk2.sheets import SheetsSync
from corte_sk2.kyoodo import PENDING, RETRY, DONE, BLOCKED, QUEUE_HEADERS, actionable_rows
from test_sheets import Book


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.s = Store(':memory:')
        self.s.bootstrap('S', 'initial123')
        self.s.change_password('S', 'initial123', 'changed123')
        self.s.save_laser('S', 'Láser 01')
        self.s.import_cpa('S', [{'Order': '900', 'Set': 'A', 'Qty': 20, 'ID': '00123'}, {'Order': '100', 'Set': 'B', 'Qty': 10, 'ID': '00999'}])
        self.book = Book()
        self.sync = SheetsSync(self.s, 'test', 'not-used')
        self.sync.book = lambda: self.book

    def tearDown(self):
        self.s.db.close()

    def report(self, key='one', order='900', line='A', date=None):
        return self.s.report('S', key, order, line, 2, comment='Comentario del operador', laser_id=1, report_date=date)

    def sync_ok(self):
        self.sync.sync()
        self.assertEqual(self.s.snapshot('S')['sync']['sync_error'], '')

    def test_atomic_idempotent_enqueue_and_oldest_first(self):
        self.report()
        self.report()
        self.report('two', '100', 'B', '2020-01-01')
        self.sync_ok()
        rows = self.book.tabs['Kyoodo'].values
        self.assertEqual(len(rows), 3)
        self.assertEqual([r[0] for r in rows[1:]], ['900', '100'])
        self.assertEqual([r[2] for r in rows[1:]], [1, 2])
        self.assertEqual(rows[1][3], '')  # El comentario del operador vive en Consolidado.
        self.assertEqual(rows[1][6], '00123')
        self.assertEqual(rows[1][8], '900|A')
        self.assertEqual([r['ID_REGISTRO'] for r in actionable_rows([rows[0], rows[2], rows[1]])], ['one', 'two'])

    def test_error_and_success_survive_repeated_sync_and_new_report(self):
        self.report()
        self.sync_ok()
        row = self.book.tabs['Kyoodo'].values[1]
        row[3], row[7] = '=Error de conexión', RETRY
        self.sync_ok()  # Incluso sin nuevos reportes, importa resultados.
        self.assertEqual(self.s.kyoodo.jobs()[0]['comment'], '=Error de conexión')
        self.assertEqual(self.s.kyoodo.jobs()[0]['state'], RETRY)
        self.report('two', '100', 'B')
        self.sync_ok()
        self.assertEqual(row[3:4], ['=Error de conexión'])
        row[7] = DONE
        self.sync_ok()
        self.sync_ok()
        self.assertEqual(self.s.kyoodo.jobs()[0]['state'], DONE)
        self.assertEqual([r['ID_REGISTRO'] for r in actionable_rows(self.book.tabs['Kyoodo'].values)], ['two'])

    def test_bot_update_between_read_and_write_is_not_clobbered(self):
        self.report()
        self.sync_ok()
        row = self.book.tabs['Kyoodo'].values[1]
        original = self.book.batch_update
        def racing(body):
            row[3], row[7] = 'Confirmado en CPA', DONE
            original(body)
        self.book.batch_update = racing
        self.report('two', '100', 'B')
        self.sync_ok()
        self.assertEqual(row[7], DONE)
        self.assertEqual(row[3], 'Confirmado en CPA')
        self.sync_ok()
        self.assertEqual(self.s.kyoodo.jobs()[0]['state'], DONE)

    def test_missing_id_blocks_then_unblocks_and_freezes_id(self):
        self.report(order='404')
        self.sync_ok()
        row = self.book.tabs['Kyoodo'].values[1]
        self.assertEqual(row[7], BLOCKED)
        self.assertFalse(actionable_rows(self.book.tabs['Kyoodo'].values))
        self.s.import_cpa('S', [{'Order': '404', 'Set': 'A', 'Qty': 20, 'ID': '00777'}])
        self.sync_ok()
        self.assertEqual(row[6:8], ['00777', PENDING])
        self.assertEqual(row[3], '')
        self.s.import_cpa('S', [{'Order': '404', 'Set': 'A', 'Qty': 20, 'ID': 'different'}])
        self.sync_ok()
        self.assertEqual(row[6], '00777')

    def test_lost_append_response_does_not_duplicate(self):
        self.report()
        self.sync_ok()
        ws = self.book.tabs['Kyoodo']
        append = ws.append_rows
        def lost(*args, **kwargs):
            append(*args, **kwargs)
            raise OSError('Response lost after commit')
        ws.append_rows = lost
        self.report('two', '100', 'B')
        self.sync.sync()
        self.assertTrue(self.s.snapshot('S')['sync']['sync_error'])
        ws.append_rows = append
        self.sync_ok()
        self.assertEqual([r[5] for r in ws.values[1:]], ['one', 'two'])

    def test_done_record_cannot_become_pending_or_reappear_as_pending(self):
        self.report()
        self.sync_ok()
        ws = self.book.tabs['Kyoodo']
        ws.values[1][7] = DONE
        self.sync_ok()
        ws.values[1][7] = PENDING
        self.sync_ok()
        self.assertEqual(ws.values[1][7], DONE)
        ws.values.pop()
        self.sync_ok()
        self.assertEqual(ws.values[1][7], DONE)

    def test_restore_keeps_completion_and_sequence(self):
        self.report()
        self.sync_ok()
        self.book.tabs['Kyoodo'].values[1][7] = DONE
        self.sync_ok()
        restored = Store(':memory:')
        sync = SheetsSync(restored, 'test', 'not-used')
        sync.book = lambda: self.book
        sync.restore_empty()
        self.assertEqual(restored.kyoodo.jobs()[0]['state'], DONE)
        self.assertEqual(restored.kyoodo.jobs()[0]['sequence'], 1)
        restored.db.close()

    def test_unknown_id_and_duplicate_remote_rows_are_rejected(self):
        self.report()
        self.sync_ok()
        row = self.book.tabs['Kyoodo'].values[1]
        self.book.tabs['Kyoodo'].values.append(list(row))
        self.sync.sync()
        self.assertIn('único', self.s.snapshot('S')['sync']['sync_error'])
        self.book.tabs['Kyoodo'].values.pop()
        row[5] = 'foreign'
        self.sync.sync()
        self.assertIn('ajeno', self.s.snapshot('S')['sync']['sync_error'])

    def test_legacy_queue_migrates_without_deleting_comments(self):
        self.report()
        ws = self.book.add_worksheet('Kyoodo', 1000, 6)
        ws.col_count = 6
        ws.values = [QUEUE_HEADERS[:6], ['900', 'Láser 01', 9, 'Nota anterior', 'A', 'one']]
        self.sync_ok()
        self.assertEqual(ws.values[1][2], 1)
        self.assertEqual(ws.values[1][3], 'Nota anterior')
        self.assertEqual(ws.values[1][6:8], ['00123', PENDING])

    def test_report_rolls_back_if_enqueue_fails(self):
        original = self.s.kyoodo.ensure_jobs
        def fail():
            raise RuntimeError('Enqueue failed')
        self.s.kyoodo.ensure_jobs = fail
        with self.assertRaises(RuntimeError):
            self.report()
        self.s.kyoodo.ensure_jobs = original
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM reports').fetchone()[0], 0)

    def test_jobs_persist_across_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.db'
            db = self.s.db
            disk = __import__('sqlite3').connect(path)
            self.report()
            db.backup(disk)
            disk.close()
            reopened = Store(path)
            self.assertEqual(reopened.kyoodo.jobs()[0]['cpa_id'], '00123')
            self.assertEqual(len(reopened.kyoodo.jobs()), 1)
            reopened.db.close()
