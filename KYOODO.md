# Contrato de integración de Kyoodo

La hoja `Kyoodo` es una cola persistente. Las nuevas filas se agregan como valores, nunca con fórmulas. El ejecutable `Kyoodo Corte SK2.exe` consume esta cola: cada computadora procesa exclusivamente su LASER_ID.

## Columnas

| Columna | Encabezado | Responsable |
| --- | --- | --- |
| A | # DE ORDEN | Servidor: orden original |
| B | LASER | Servidor: nombre al reportar |
| C | SECUENCIA | Servidor: entero estable; menor = más viejo |
| D | COMENTARIO | Bot: error o detalle del resultado. Inicialmente vacío, salvo motivo de bloqueo |
| E | LINEA | Servidor: set |
| F | ID_REGISTRO | Servidor: clave única e inmutable del reporte |
| G | ID_CPA | Servidor: ID original del maestro para orden + línea |
| H | ESTADO | Bot en filas habilitadas; servidor para creación, desbloqueo y protección de confirmaciones |
| I | ORDEN_LINEA | Servidor: `ORDEN|SET`, como valor |
| J | FECHA_REGISTRO | Servidor: hora real de recepción, con zona horaria |
| K | STATUS_DESTINO | Servidor: 910 |
| L | LASER_ID | Servidor: clave para buscar correo y contraseña en Cuentas CPA |

`COMENTARIO` es del bot. El comentario del operador permanece en el consolidado y en la vista de su láser.

## Estados

- `PENDIENTE`: preparado para intentar actualizar CPA.
- `REINTENTAR`: falló; D describe el motivo. Se vuelve a intentar en el siguiente ciclo.
- `BLOQUEADA SIN ID CPA`: no ejecutar. El servidor resolverá el ID al actualizar el maestro; entonces escribirá ID_CPA y PENDIENTE juntos.
- `YA EN 910`: CPA ya estaba en el destino; no se hizo ningún cambio. Resultado definitivo.
- `SUPERIOR A 910`: CPA está por encima del destino; no se hizo ningún cambio. El comentario incluye el estado encontrado. Resultado definitivo.
- `ACTUALIZADA A 910`: guardado confirmado después de recargar; excluir de próximas ejecuciones. No eliminar la fila ni reutilizar su ID_REGISTRO.

Una fila representa un reporte. Dos reportes distintos de la misma orden y línea conservan dos identificadores de reporte. El estado 910 en CPA se debe comprobar antes de repetir la actualización, para que estos reportes o una respuesta perdida no causen acciones duplicadas.

## Ciclo de trabajo del bot

1. Leer encabezados y filas; usar `corte_sk2.kyoodo.actionable_rows(values)` para obtener PENDIENTE/REINTENTAR con ID, ordenadas por SECUENCIA ascendente.
2. Dar como máximo un intento a cada fila en ese ciclo y continuar con las siguientes si una falla. El siguiente ciclo vuelve a leer la hoja.
3. Buscar correo y contraseña por LASER_ID en Cuentas CPA. Si falta acceso, escribir el error y REINTENTAR; no cambiar de cuenta arbitrariamente.
4. Volver a localizar la fila por **ID_REGISTRO** y comprobar que siga habilitada antes de actuar. No guardar números de fila entre ciclos.
5. Leer en CPA la fila con ID_CPA y comprobar que corresponda a la orden y línea esperadas. Si ya está en 910 o en un estado superior, cerrar con su estado y comentario sin modificar CPA. Si es inferior, habilitar edición mediante el botón real, seleccionar value=91, guardar, esperar a salir de edición, recargar y verificar 0910 en el mismo ID.
6. Volver a localizar ID_REGISTRO y escribir D y H como valores RAW en una misma solicitud de Google. Nunca marcar éxito antes de verificarlo en CPA.
7. Ante un fallo, guardar el motivo en D y `REINTENTAR` en H. Ante éxito confirmado, guardar una nota o dejar D vacío y escribir `ACTUALIZADA A 910` en H.

No escribir el error en otras columnas; no incluir contraseñas en mensajes de error. No deducir trabajo pendiente solo del comentario: **el estado gobierna los reintentos**.

## Concurrencia y preservación

Ejecutar una sola instancia por LASER_ID. Diferentes láseres trabajan en paralelo. Cada proceso mantiene un bloqueo del sistema operativo en la misma carpeta UNC compartida; una segunda instancia del mismo láser se detiene. No usar directorios de bloqueo distintos entre equipos. El bloqueo se libera al terminar o morir el proceso. No ordenar físicamente, borrar ni insertar filas intermedias mientras trabaja. Para inspección, usar vistas de filtro sin reordenar la fuente compartida. El servidor únicamente agrega filas y no reescribe D/H de trabajos habilitados durante una sincronización normal.

Si el bot escribe un resultado mientras el servidor sincroniza, ese resultado se leerá en el siguiente ciclo y se conservará en el respaldo. Una confirmación guardada no puede volver a pendiente; si se cambia por error, el servidor restablece el estado definitivo conservado. Si se elimina una fila cuyo resultado ya se había sincronizado, se repone con el estado conservado. Si se borra antes de sincronizar el resultado, ese resultado no puede recuperarse: por eso el contrato exige conservar las filas.

Un envío al Sheet cuya respuesta se pierde se verifica por ID_REGISTRO antes de volver a agregarlo. Cualquier ID_REGISTRO ajeno o duplicado detiene la sincronización para evitar sobrescribir datos de otra base.

Los ID_CPA asignados no cambian al importar otro maestro. Los bloqueados sí se resuelven cuando el ID aparece. El estado destino de esta integración es fijo en 910.

## Recuperación y cambio de versión

El respaldo técnico v2 incluye `kyoodo_jobs`, sus secuencias y resultados. La recuperación lee primero los resultados que sigan en la hoja antes de publicar nuevas filas. Los respaldos v1 se pueden recuperar; sus reportes se convierten en trabajos pendientes o bloqueados según el ID disponible. Si ya hubo procesamiento fuera de esta cola, revisar CPA antes de ejecutar esa migración.

No ejecutar el sincronizador antiguo junto al nuevo: el antiguo regenera Kyoodo y puede borrar sus resultados. Actualizar y reiniciar la única instancia central.


## Preparar cada computadora

1. Actualizar primero el servidor central a esta versión: reconoce los dos nuevos resultados definitivos. Detener versiones antiguas del sincronizador y del bot.
2. Copiar el paquete Windows, con `Kyoodo Corte SK2.exe`, los dos CMD de Kyoodo y `kyoodo.example.json`. Chrome debe estar disponible; no requiere instalar Python si se usa el EXE.
3. Copiar `kyoodo.example.json` como `kyoodo.json`. Establecer `laser_id` con el LASER_ID exacto de Configuración (no su nombre visible), y el mismo `spreadsheet_id` de `server.json`.
4. Configurar `credentials` apuntando al JSON de la cuenta de servicio autorizada como editor del Sheet. Puede ser una ruta absoluta accesible desde ese equipo. No incluir este JSON ni contraseñas en GitHub. El bot obtiene correo y contraseña de la fila de ese láser en Cuentas CPA; LASER_ID de la cola es su enlace a esa cuenta.
5. Todos los equipos deben usar exactamente el mismo `lock_directory`, por defecto `\\cs-600\Control_Produccion_Data\Kyoodo Corte\locks`. La cuenta Windows necesita escritura y soporte de bloqueos SMB; no borrar archivos lock mientras trabajan los bots.
6. `edit_button_selector` debe ser el selector CSS del botón real que habilita editar las líneas en CPA. El fragmento aportado solo contiene controles deshabilitados, por lo que este valor queda vacío deliberadamente. Sin él, las filas que requieren cambios quedan en REINTENTAR con ese motivo; las que ya están en 0910 o superior se cierran sin cambios. No se retira el atributo disabled ni se fuerza JavaScript.
7. Ejecutar `Comprobar-Kyoodo.cmd`: verifica acceso al Sheet, cuenta configurada y pendientes propios; no abre ni modifica CPA. Esta comprobación no valida la contraseña con CPA.
8. Ejecutar `Iniciar-Kyoodo.cmd`. Mantener su consola abierta. Ctrl+C detiene el bot y libera el bloqueo. El intervalo mínimo entre ciclos es 30 segundos, por defecto 60. Un fallo no impide intentar las siguientes filas del mismo ciclo.

El navegador inicia una sesión nueva por trabajo, usando la cuenta actual del láser. Los campos de login se verificaron en la página pública (`Email Address`, `Enter Password`). Si cambian, configurar sus selectores. Para Chrome portable, establecer `chrome_binary`; para evitar la descarga automática del controlador por Selenium Manager, establecer `chromedriver` con un ejecutable compatible. No se guarda la contraseña en el navegador.

Desde código fuente: `python -m pip install -r requirements-kyoodo.txt`, luego `python run_kyoodo.py --config kyoodo.json`. `--check` solo inspecciona; `--once` ejecuta un ciclo real con escrituras en CPA.

## Guardado y límites de verificación

Se localiza el perfil por orden, la fila por ID_CPA textual exacto y se verifica el Set. Se reconoce el selector principal por su opción 0910/value=91. Se comparan los códigos visibles, no los valores internos: por ejemplo 1050 tiene value=150.5. Se relee la fila después de habilitar edición y se vuelve a validar el trabajo de la cola antes de guardar.

Se espera a que tanto el botón de guardado como el selector queden deshabilitados al terminar la edición y solo entonces se recarga. Si CPA usa otra señal al terminar, la espera fallará y se registrará REINTENTAR sin declarar éxito. Este comportamiento y el botón de edición requieren una comprobación real con la cuenta autorizada; las pruebas automatizadas usan dobles del navegador y de Sheets, no actualizan producción.

Una respuesta perdida se resuelve leyendo primero CPA y la cola en el siguiente intento. Si una persona cambia la orden en CPA simultáneamente, la interfaz web no ofrece una actualización condicional atómica: coordinar para no editar la misma línea mientras Kyoodo la procesa. No ordenar físicamente ni borrar filas de la cola durante las escrituras; usar vistas de filtro.
