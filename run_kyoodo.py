from corte_sk2.kyoodo_worker import main

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        # No imprimir excepciones externas que podrían contener credenciales.
        print(str(exc) if isinstance(exc, ValueError) else 'No se pudo iniciar Kyoodo. Revisa configuración, conexión y permisos.')
        raise SystemExit(1)
