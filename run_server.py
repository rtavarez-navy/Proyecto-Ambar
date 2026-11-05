"""El servidor local dejó de ser el modo de operación de Corte."""
import os

if __name__ == '__main__':
    if os.environ.get('CORTE_LEGACY_RECOVERY') == '1':
        from corte_sk2.server import main
        main()
    else:
        raise SystemExit('Corte ahora usa Google Sheets mediante HTTPS. Lee CLOUD.md. No iniciar el servidor local sobre el Sheet activo.')
