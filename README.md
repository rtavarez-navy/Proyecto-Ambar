# Corte SK2 2027 · Google Sheets

Aplicación portátil de Windows en Python, negro y verde neón. **Ya no necesita una PC encendida como servidor central**: el cliente se conecta por HTTPS a un servicio Python alojado en Google Cloud Run. **Google Sheets es la base de datos central**, sin una base SQLite operativa en planta.

> Esta versión está preparada para desplegar. No trae una dirección de servicio publicada: primero debe completarse [CLOUD.md](CLOUD.md), configurar el proyecto y probar el servicio real. La conexión de Google de ChatGPT no autoriza automáticamente al ejecutable.

## En cada PC

1. Descargar el artefacto de la última ejecución exitosa en GitHub Actions → Ejecutables portátiles Windows.
2. Extraer y abrir `Corte SK2 2027.exe`.
3. Pegar la dirección HTTPS del servicio desplegado. La dirección se recuerda por usuario de Windows.
4. Entrar con el código del empleado; la primera clave es `123456` y obliga a cambiarla a una nueva de al menos 8 caracteres.

Opcionalmente, copiar `client.example.json` como `CorteSK2.json`, colocar allí la dirección real y dejarlo junto al ejecutable en todas las PC. No contiene contraseñas ni claves administrativas. También se puede configurar `CORTE_API_URL`.

Las PC necesitan Internet. No necesitan instalar Python ni guardar un JSON de Google. La aplicación no usa `localhost`, carpetas compartidas ni un proceso servidor en la PC del supervisor. No volver a ejecutar el sincronizador local antiguo sobre el mismo Sheet.

## Datos y permisos

| Hoja | Uso |
| --- | --- |
| `Maestro` | Nombres y códigos de empleados. Se lee sin modificarla. Actualizar el nombre no reinicia claves. |
| `Configuración` | Láseres, nombres de hojas, orden y ACTIVO. Agregar o desactivar conserva el historial. |
| `Consolidado` | Reportes: FECHA, # DE ORDEN, LINEA, # PIEZAS, COMENTARIOS y trazabilidad. |
| Hojas por láser | Vistas del consolidado; cada operador solo recibe los datos de su láser asignada. |
| `Kyoodo` | Cola persistente, ID CPA, destino 910, estados y reservas temporales por trabajo. |
| `Cuentas CPA` | Láser, correo y contraseña de su cuenta CPA. Las credenciales son legibles en el Sheet por autorización del responsable. |
| `Asignaciones` | Relación entre empleados y láseres; se puede gestionar por arrastre desde supervisión. |
| `CPA DATA` | Order, Set, piezas e ID original. Se conservan las fórmulas de importación existentes. |
| `_Usuarios` | Códigos, hash de clave, rol y versión de acceso. |
| `_Sesiones` | Hashes de tokens de sesión, vencimiento y versión de clave. |
| `_Accesos` | Control persistente de intentos fallidos de acceso. |
| `_Auditoria` | Registro acumulativo de operaciones. |

Las hojas técnicas se ocultan, pero ocultarlas **no** es una barrera de seguridad frente a editores del archivo. Solo supervisión y la identidad del servicio deben acceder al Sheet; los operadores acceden únicamente a través de la aplicación.

El servicio valida la sesión y la asignación en cada solicitud. Un operador no puede elegir otra láser cambiando los datos enviados desde su PC. Una nueva clave invalida las otras sesiones del mismo código. Las credenciales CPA de otra láser no se devuelven a operadores.

## Registro y validación

Un envío guarda el reporte y su trabajo Kyoodo **en la misma solicitud atómica de Sheets**, antes de responder que quedó guardado. Si se pierde la respuesta, el cliente conserva el identificador y permite reintentar sin duplicarlo. No cerrar la aplicación mientras haya un envío sin confirmar; si se cerró, revisar el reporte antes de capturarlo nuevamente.

| Situación | Resultado |
| --- | --- |
| Misma orden + línea en una o varias láseres | Todas las filas involucradas en rojo |
| Piezas acumuladas superiores al CPA | Rojo y «Orden adulterada» |
| Orden o línea inexistente en CPA | Amarillo y mensaje de revisión; si además está duplicada, prevalece rojo |
| Falta el ID CPA | Reporte conservado, trabajo Kyoodo bloqueado hasta resolverlo |

El acumulado considera todo el historial. Los totales incluyen las filas con incidencias. La fecha de producción es editable y se guarda separada de la hora real de recepción. Se admite `ORDEN|SET` en el campo de orden.

La aplicación consulta novedades automáticamente cada 60 segundos para moderar el consumo de cuota; registrar producción actualiza la pantalla con la respuesta del mismo envío. También hay actualización manual. No se necesita un sincronizador de fondo: los datos están en Sheets al confirmar el guardado.

## Kyoodo por láser

Ver [KYOODO.md](KYOODO.md). El ejecutor debe autenticarse y usar `/kyoodo/next` y `/kyoodo/result`. Solo recibe trabajos y credenciales de su láser. El servicio reserva un trabajo durante 10 minutos para impedir que dos ejecutores tomen la misma orden simultáneamente.

No usar el consumidor anterior que escribía directamente en las celdas: no participa del bloqueo de escrituras. La automatización RPA que navega y modifica el CPA real sigue siendo una integración separada; este repositorio expone su contrato y control de cola.

## Despliegue y migración

Seguir [CLOUD.md](CLOUD.md). El servicio usa una cuenta de servicio de Google adjunta a Cloud Run, **sin descargar ni repartir claves JSON**. Requiere proyecto, APIs, permisos y configuración de facturación de Google Cloud; no se garantiza coste cero.

Las escrituras se coordinan con un objeto privado en Cloud Storage, que contiene únicamente metadatos de bloqueo. La producción, usuarios, sesiones, asignaciones y resultados permanecen en Sheets. El bucket no se usa como otra base de producción.

Si existe `_Estado` de la versión local, la primera inicialización migra sus hashes de acceso y asignaciones al nuevo formato, conservando los reportes y resultados ya presentes en las hojas. Primero sincronizar y detener la versión local. Los reportes que todavía estén solo en el disco deben entregarse a Sheets antes de migrar.

No borrar ni reordenar físicamente filas de las tablas mientras opera el servicio. Las modificaciones desde la aplicación y el bot pasan por el servicio. Durante mantenimiento, detener el tráfico antes de editar manualmente producción o sesiones.

## Desarrollo

Python 3.11 o posterior:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt Flask google-cloud-storage
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe run_client.py
```

Para servir la API se usa la imagen `Dockerfile` en Cloud Run. El módulo es `corte_sk2.cloud:create_app()`. El arranque se prueba desde Linux con Gunicorn; no se requiere ese proceso en las PC Windows.

Los módulos SQLite y el sincronizador antiguo se conservan para pruebas y recuperación de la versión previa. No forman parte del almacenamiento del nuevo servicio y el ejecutable de servidor local ya no se compila ni se distribuye.

## Verificación pendiente en Google y planta

Las pruebas automatizadas usan una implementación simulada de Sheets y prueban autenticación, atomicidad, concurrencia, recuperación de sesión y reservas. Aún se debe verificar el despliegue real, permisos, cuota con las 11 láseres, tiempos de respuesta, ejecutable Windows y ejecución del bot contra CPA.
