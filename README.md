# Corte SK2 2027

Aplicación portátil para Windows en Python + Tkinter, negro y verde neón. Reporte provisional de corte independiente de Ámbar.

## Configurar el Sheet de la planta

El enlace del Sheet se guarda únicamente en el `server.json` privado de la computadora central. No se publica en el repositorio. **Falta proporcionar ese enlace y una credencial local de Google y probar el servicio en la computadora central.** La cuenta conectada a ChatGPT permite preparar el Sheet desde una conversación, pero no se transfiere a los ejecutables.

| Hoja | Dirección de los datos |
| --- | --- |
| `Maestro` | Entrada. Lee `APELLIDOS_NOMBRES` y `CODIGO_EMPLEADO`; no modifica la hoja. Agrega operadores nuevos con `123456` y actualiza nombres sin reiniciar claves ni asignaciones. No elimina usuarios por retirarlos del maestro. |
| `Configuración` | Entrada. Catálogo `LASER_ID`, `NOMBRE`, `HOJA`, `ORDEN`, `ACTIVO`. Catálogo configurable. También refleja cambios realizados desde la aplicación. |
| `Consolidado` | Salida. Todos los reportes y sus alertas. No editar producción manualmente aquí. |
| `Láser 01` … `Láser 11` | Vistas automáticas del consolidado filtradas por LASER_ID. No se escriben reportes manualmente en estas hojas. |
| `Kyoodo` | Salida. Órdenes de mayor a menor, láser, posición, comentario, línea e identificador. No ejecuta acciones CPA. |
| `Cuentas CPA` | Entrada/salida. Cuenta CPA de referencia por LASER_ID, sin contraseñas. Se puede editar en la hoja o en la aplicación. |
| `Asignaciones` | Salida. Códigos y láseres asignadas por el supervisor. |
| `CPA DATA` | Salida del maestro CPA importado desde la aplicación. |
| `_Estado` | Respaldo técnico oculto, creado por el servidor al sincronizar. Incluye hashes de acceso e historial. |

La sincronización consulta las entradas cada 60 segundos, incluso si no hubo reportes nuevos. Un cambio pendiente de láser o cuenta CPA realizado en la aplicación tiene prioridad en el próximo ciclo; evitar editar ese mismo catálogo simultáneamente desde ambos lugares.

No compartir el Sheet con operadores ni compartir el JSON de Google. Ocultar `_Estado` no protege su contenido frente a personas con acceso al archivo.

## Descargar los programas

GitHub → **Actions → Ejecutables portátiles Windows → ejecución exitosa → Artifacts → Corte-SK2-2027-Windows**. Descargar y extraer el ZIP.

- `Corte SK2 2027.exe`: cliente de operadores y supervisión.
- `Servidor Corte SK2 2027.exe`: un único servicio central para todas las terminales.
- `Conectar-Google.cmd`: asistente de conexión de Google.
- `Iniciar-servidor.cmd`: inicia el servicio desde su carpeta.

No necesitan instalar Python. Los ejecutables no están firmados digitalmente. Comprobar compilación, arranque y funcionamiento en Windows antes de distribuir a planta.

## Conectar Google en la computadora central

1. Crear una cuenta de servicio en Google Cloud y habilitar Google Sheets API y Google Drive API en su proyecto. Descargar su clave JSON al equipo central; no enviarla por chat ni subirla al repositorio.
2. Extraer los ejecutables en una carpeta del disco local con permiso de escritura. Ejecutar `Conectar-Google.cmd`.
3. El asistente pide la ruta del JSON y el enlace de tu Sheet, guarda una copia local como `google-service-account.json` y muestra el correo de la cuenta de servicio.
4. Compartir el Sheet de corte como **Editor** con ese correo. Volver a la consola y pulsar Enter. El asistente verifica el acceso y las hojas esperadas.
5. Si es un servidor nuevo, introducir el código del supervisor y una clave inicial de al menos 8 caracteres. El supervisor deberá cambiar esa clave en la primera entrada a la aplicación.
6. Mantener el servicio abierto. Abrir el cliente en ese equipo con `http://localhost:8765`; desde otras terminales, usar `http://NOMBRE-PC-CENTRAL:8765` o su IP fija. Permitir el puerto TCP 8765 solamente en la red privada de planta.
7. Entrar como supervisor, cambiar clave, pulsar «Sincronizar Sheets», comprobar los empleados y las 11 láseres, asignar operadores por arrastre y cargar el maestro CPA.
8. Comprobar un reporte y su sincronización en el consolidado, la láser correspondiente y Kyoodo antes de usarlo en producción.

Si ya existe un `server.json` de una versión anterior con `spreadsheet_id` vacío, ejecutar el asistente para completar esa configuración. Las rutas relativas se resuelven respecto a `server.json`.

Para conectarlo desde Python:

```powershell
.\.venv\Scripts\python.exe run_server.py --connect-google
```

## Operación

El operador entra con código de empleado y clave inicial `123456`; debe cambiarla a una nueva de al menos 8 caracteres. Solo puede reportar en su láser asignada. Sin asignación, aparece el mensaje de espera.

Los cinco campos del reporte son **FECHA, # DE ORDEN, LINEA, # PIEZAS y COMENTARIOS**. La fecha se introduce como `AAAA-MM-DD` y es independiente de la hora real de registro. La orden admite `ORDEN|SET`. Reprint se conserva como dato adicional.

El supervisor ve producción diaria, gráficos, duplicados y alertas. Puede gestionar láseres, asignar empleados y cargar CPA `.db`, `.csv` UTF-8 o `.xlsx`. En la base `.db`, se usa la tabla `cpa_lines` con columnas entrecomilladas `Order`, `Set`, `Prod Line Pieces #` o `Qty`; nunca `Order Prod Lines #` como cantidad.

| Validación | Resultado |
| --- | --- |
| Misma orden + línea en una o varias láseres | Todas las filas involucradas en rojo, indicando láseres |
| Suma de piezas de orden + línea superior al CPA | Rojo y «Orden adulterada» |
| Orden o línea inexistente | Amarillo y mensaje de revisión; si también es duplicada, prevalece rojo |

Las alertas se conservan y no bloquean la captura. Los totales incluyen las filas con incidencias. El acumulado y los duplicados consideran todo el historial, no solo el día. Al cambiar el maestro CPA se recalculan las alertas.

## Agregar o quitar láseres

Desde la aplicación: **LÁSERES Y EQUIPO → Nueva láser**, o seleccionar una y usar **Activar / desactivar**. Se conserva el historial y al desactivar se quitan las asignaciones.

Desde `Configuración`: agregar una fila con un LASER_ID único estable (por ejemplo `LASER_12`), nombre, nombre de hoja exclusivo, orden entero y ACTIVO marcado. El servicio creará la vista automáticamente. Desmarcar ACTIVO para quitarla de la captura; no borrar ni reutilizar identificadores históricos. Las hojas históricas no se eliminan automáticamente.

## Almacenamiento y recuperación

Un servicio central conserva un **almacén transaccional SQLite en disco local** y sincroniza con Sheets. Esta versión usa el Sheet como directorio, configuración, reporte compartido y respaldo; **no es un programa con Google Sheets como único almacenamiento**. Las terminales se conectan al servicio y no reciben credenciales de Google.

Usar una sola instancia central. No colocar la base SQLite activa en una carpeta SMB. No exponer el puerto HTTP a Internet; para proteger credenciales en tránsito, usar un proxy HTTPS y su URL en los clientes.

La pantalla diferencia guardado local, pendiente de sincronización y última sincronización. Si encuentra registros en el consolidado ajenos a la base local, detiene las escrituras en lugar de sobrescribirlos. Recuperar el respaldo existente antes de continuar.

Conservar copias de seguridad de `datos/corte.db` con el servicio cerrado. Si se pierde la base, preparar una configuración apuntando al mismo Sheet y a una base inexistente, y ejecutar:

```powershell
& '.\Servidor Corte SK2 2027.exe' --restore-sheet
```

Restaura solamente sobre una base vacía. Recupera hasta el último respaldo completado; las filas pendientes requieren recuperar el disco local original. No editar `_Estado`.

## Desarrollo y pruebas

Python 3.11 o posterior:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe run_server.py
```

En otra consola: `.\.venv\Scripts\python.exe run_client.py`.

GitHub Actions instala dependencias, ejecuta las pruebas y compila ambos ejecutables con PyInstaller.

## Límites actuales

- No captura filas nuevas sin conexión al servicio. Si una respuesta se pierde, reintentar sin modificar el formulario conserva el mismo identificador y evita duplicar. No cerrar el cliente hasta resolver un envío pendiente.
- Las correcciones auditadas de producción, aprobación de reprints y recuperación de claves olvidadas aún no tienen interfaz.
- La visualización del cliente se actualiza cada 5 segundos; Sheets cada 60. Medir rendimiento y cuotas con el volumen real antes del despliegue general.
- Las pruebas de sincronización usan un adaptador de Google simulado. El acceso real desde el ejecutable requiere la configuración local descrita arriba; debe verificarse también con escáneres y pantallas de planta.
