# Corte SK2 2027

Aplicación de escritorio portátil en **Python + Tkinter**, negro y verde neón, para el reporte provisional de corte de Sakira 2. Independiente de Ámbar, sin Supabase ni integración automática con CPA.

## Qué incluye

- Operadores con código de empleado y clave inicial `123456`. Cambio obligatorio a una clave nueva de 8 caracteres como mínimo. Contraseñas PBKDF2 con sal individual; no se guardan contraseñas en texto plano.
- Supervisor inicial creado en la consola del servicio; sin credenciales administrativas predeterminadas.
- Gestión de láseres y empleados. Asignación por arrastre o selección. Un empleado pertenece a una láser; una láser puede tener varios operadores.
- Un operador sin asignación ve el mensaje de espera. Los permisos se validan en servidor en cada operación.
- Captura de orden, línea/set, piezas, comentario y reprint. Admite pegar/escanear `ORDEN|SET` en el campo de orden.
- Reporte propio de cada láser, resumen diario y gráficos de piezas por láser para supervisión. Doble clic en una fila para leer la validación completa.
- Duplicados por **orden + línea/set**, tanto dentro de una láser como entre láseres, sobre todo el historial. Se marcan todas las filas involucradas en rojo.
- Cantidades acumuladas superiores al CPA: rojo y «Orden adulterada». Una marca de reprint se registra, pero no exime de alertas.
- Orden o línea inexistente: amarillo y mensaje de revisión. Si además es duplicada, prevalece rojo y se muestran ambos motivos.
- Carga de CPA desde `.db` (tabla `cpa_lines`), `.csv` UTF-8 o `.xlsx`. Usa `Prod Line Pieces #`, con alternativa `Qty`; no usa `Order Prod Lines #`. Una importación inválida conserva la base anterior.
- Reintento con identificador único: un envío cuya respuesta se perdió no crea un segundo registro si se reintenta sin cambiar los datos.
- Registro interno de creación, asignaciones, cambios de clave, importaciones y reportes; sin API para editar o borrar producción en esta primera versión.

## Arquitectura y Google Sheets

Ejecutar **un solo servicio central** en una computadora estable de planta. Todas las copias de `Corte SK2 2027.exe` se conectan a él por la red local.

El servicio conserva un **almacén transaccional SQLite local** y proyecta los datos a Google Sheets cada 60 segundos. Esto permite escrituras simultáneas, controlar permisos y reintentar Google sin perder producción ya aceptada. Sheets contiene los reportes y un respaldo recuperable. **Esta versión no funciona con Sheets como único almacenamiento ni importa cambios manuales hechos en sus celdas.** Las hojas administradas se sobrescriben al sincronizar; usar un archivo dedicado.

La pantalla distingue «guardado en el servidor», «pendiente de sincronizar» y la última sincronización exitosa. Sin configuración de Google, los registros permanecen locales y el estado seguirá pendiente. No usar la versión como reporte central definitivo hasta completar y probar esa configuración.

| Hoja administrada | Contenido |
| --- | --- |
| `Laser 1`, `Laser 2`, etc. | Una hoja por id estable de láser, con nombre real en las filas, producción y alertas |
| `Kyoodo` | Orden descendente por número de orden; láser, posición en esa secuencia, comentario, línea e id del reporte |
| `Asignaciones` | Código, nombre y láser asignada |
| `Cuentas CPA` | Nombre de láser y cuenta de referencia; no guarda claves ni ejecuta acciones CPA |
| `CPA DATA` | Order, Set y piezas del maestro importado |
| `_Estado` (oculta) | Respaldo técnico para recuperación, incluyendo hashes de acceso e historial de auditoría |

No compartir el Sheet ni el JSON de Google con operadores. La hoja oculta no es una barrera de seguridad para personas con acceso al archivo. Los operadores solo necesitan el ejecutable cliente y la dirección del servicio.

## Obtener los ejecutables

En GitHub: **Actions → Ejecutables portátiles Windows → ejecución exitosa → Artifacts → Corte-SK2-2027-Windows**. Descargar y extraer el ZIP. Los artefactos requieren iniciar sesión en GitHub y se conservan 30 días.

Incluye:

- `Corte SK2 2027.exe`: cliente, sin instalación ni Python preinstalado.
- `Servidor Corte SK2 2027.exe`: servicio central portátil, con consola.
- Configuración de ejemplo y esta guía.

El flujo se ejecuta en cada push a `main` y también manualmente desde Actions. Los ejecutables no están firmados digitalmente. La compilación y ejecución en Windows deben comprobarse antes de distribuir a planta.

## Primera puesta en marcha

1. Extraer el servidor en una carpeta con permiso de escritura **en el disco local** de la computadora central. No colocar la base SQLite activa en una carpeta SMB ni iniciar varias instancias compartiendo la misma base.
2. Ejecutar `Servidor Corte SK2 2027.exe`. Se crea `server.json`; la consola pide el código y una clave inicial de al menos 8 caracteres para el supervisor. Guardar esa clave. En la primera entrada a la aplicación también deberá cambiarla.
3. Permitir conexiones entrantes al puerto TCP `8765` solamente desde la red privada de planta. Mantener el servicio abierto.
4. Abrir el cliente. En «Servidor central», usar `http://NOMBRE-PC-CENTRAL:8765` o su IP fija; `localhost` sirve solo desde la propia computadora central. La dirección se recuerda por usuario de Windows.
5. Entrar como supervisor, cambiar clave, crear láseres y empleados, asignar empleados por arrastre, cargar la base CPA y verificar un registro de prueba.
6. En cada terminal, abrir el cliente y entrar con código de empleado y `123456`; cambiar la clave y comprobar la láser asignada.

El servicio usa HTTP en una red privada confiable por defecto. Para conexiones fuera de esa red o protección de credenciales en tránsito, publicar el servicio detrás de un proxy HTTPS y usar su URL `https://` en los clientes. No exponer el puerto directamente a Internet.

## Conectar el Google Sheet

1. Crear un Sheet **dedicado y vacío** para Corte SK2.
2. Crear una cuenta de servicio de Google Cloud y habilitar Google Sheets API y Google Drive API.
3. Guardar su archivo JSON **solo en la computadora central**, como `google-service-account.json`, junto a `server.json`. No subirlo al repositorio.
4. Compartir el Sheet como editor con el correo `client_email` de esa cuenta de servicio.
5. Cerrar el servidor. Editar `server.json` y poner en `spreadsheet_id` el id del enlace: `https://docs.google.com/spreadsheets/d/ESTE_ID/edit`.
6. Reiniciar el servidor. Entrar como supervisor y pulsar «Sincronizar Sheets». Comprobar la última sincronización y las hojas creadas. Si falta un permiso o hay una cuota temporal, los datos permanecen locales y se reintenta.

```json
{
  "host": "0.0.0.0",
  "port": 8765,
  "database": "datos/corte.db",
  "spreadsheet_id": "ID_DEL_SHEET",
  "credentials": "google-service-account.json"
}
```

Las rutas relativas se resuelven respecto a `server.json`. Para otra ubicación de configuración, ejecutar el servidor con `--config RUTA`.

## Recuperación

Conservar respaldos de la carpeta de datos del servidor. Para una copia manual del archivo `datos/corte.db`, cerrar el servidor primero; no copiar únicamente el `.db` mientras está escribiendo en modo WAL.

Si se pierde la base local: preparar un directorio nuevo con el JSON de Google y un `server.json` apuntando al mismo Sheet y a una base inexistente. Ejecutar:

```powershell
& '.\Servidor Corte SK2 2027.exe' --restore-sheet
```

Solo restaura sobre una base vacía y recupera el último respaldo de Sheets completado. Los registros todavía pendientes de sincronizar requieren recuperar el disco/base local original. El backup técnico no debe editarse manualmente.

## Desarrollo

Python 3.11 o posterior. Desde la raíz del repositorio:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_server.py
```

En otra consola:

```powershell
.\.venv\Scripts\python.exe run_client.py
```

Pruebas:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Límites de esta primera versión

- No captura nuevas filas sin conexión al servicio. Un reintento sin confirmar se mantiene durante la sesión del cliente; no cerrar la aplicación hasta resolverlo. Si se cierra, revisar el reporte antes de capturar otra vez.
- Las alertas señalan incidencias y conservan el reporte; no bloquean su registro. Los totales de producción incluyen esas filas. No existe todavía un proceso de aprobación de reprints o corrección auditada.
- El acumulado se calcula sobre todo el historial cargado, incluso si la base CPA se reemplaza. Importar siempre el maestro que corresponda al alcance del reporte.
- El supervisor crea operadores; esta versión no permite crear supervisores adicionales, recuperar claves olvidadas ni eliminar usuarios desde la interfaz.
- El reporte se actualiza por consulta cada 5 segundos. Para despliegues grandes, medir rendimiento y cuotas con el número real de láseres y filas antes del uso general.
- Pendiente de validación en planta: conexión Google real, escáneres, resolución de pantalla y ejecutables Windows.
