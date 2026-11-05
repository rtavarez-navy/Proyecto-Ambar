"""Lectura de reportes CPA sin modificar los archivos originales."""
import csv
import sqlite3
from pathlib import Path


def read_cpa(filename):
    path = Path(filename)
    suffix = path.suffix.lower()
    if suffix in ('.db', '.sqlite', '.sqlite3'):
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            cols = {r[1] for r in db.execute('PRAGMA table_info("cpa_lines")')}
            qty = 'Prod Line Pieces #' if 'Prod Line Pieces #' in cols else 'Qty'
            if not {'Order', 'Set', qty} <= cols:
                raise ValueError('cpa_lines debe contener Order, Set y Prod Line Pieces # o Qty.')
            quantities = [name for name in ('Prod Line Pieces #', 'Qty', 'ID') if name in cols]
            query = 'SELECT "Order", "Set", ' + ', '.join(chr(34) + name + chr(34) for name in quantities) + ' FROM "cpa_lines"'
            return [dict(row) for row in db.execute(query)]
    if suffix == '.xlsx':
        from openpyxl import load_workbook
        book = load_workbook(path, read_only=True, data_only=True)
        try:
            sheet = book['CPA DATA'] if 'CPA DATA' in book.sheetnames else book.active
            rows = sheet.iter_rows(values_only=True)
            for row in rows:
                headers = [str(v).strip() if v is not None else '' for v in row]
                if 'Order' in headers and 'Set' in headers:
                    return [dict(zip(headers, row)) for row in rows]
            raise ValueError('No se encontró una cabecera Order / Set en el Excel.')
        finally:
            book.close()
    if suffix == '.csv':
        with path.open(encoding='utf-8-sig', newline='') as stream:
            sample = stream.read(8192)
            stream.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
            except csv.Error:
                dialect = csv.excel
            return [dict(row) for row in csv.DictReader(stream, dialect=dialect)]
    raise ValueError('Selecciona un archivo CPA .db, .csv o .xlsx.')

