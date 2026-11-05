# Contrato de integración de Kyoodo

La hoja `Kyoodo` es una cola persistente. Las nuevas filas se agregan como valores, nunca con fórmulas. El bot existente debe adaptarse a este contrato antes de habilitarlo contra CPA; esta aplicación todavía no ejecuta la automatización RPA.

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
- `ACTUALIZADA A 910`: confirmación definitiva, excluir de próximas ejecuciones. No eliminar la fila ni reutilizar su ID_REGISTRO.

Una fila representa un reporte. Dos reportes distintos de la misma orden y línea conservan dos identificadores de reporte. El estado 910 en CPA se debe comprobar antes de repetir la actualización, para que estos reportes o una respuesta perdida no causen acciones duplicadas.

## Ciclo de trabajo del bot

1. Leer encabezados y filas; usar `corte_sk2.kyoodo.actionable_rows(values)` para obtener PENDIENTE/REINTENTAR con ID, ordenadas por SECUENCIA ascendente.
2. Dar como máximo un intento a cada fila en ese ciclo y continuar con las siguientes si una falla. El siguiente ciclo vuelve a leer la hoja.
3. Buscar correo y contraseña por LASER_ID en Cuentas CPA. Si falta acceso, escribir el error y REINTENTAR; no cambiar de cuenta arbitrariamente.
4. Volver a localizar la fila por **ID_REGISTRO** y comprobar que siga habilitada antes de actuar. No guardar números de fila entre ciclos.
5. Leer en CPA la fila con ID_CPA y comprobar que corresponda a la orden y línea esperadas. Si ya está en 910, confirmar sin volver a modificarla. Si no, actualizar a 910 y verificar el resultado en CPA.
6. Volver a localizar ID_REGISTRO y escribir D y H como valores RAW en una misma solicitud de Google. Nunca marcar éxito antes de verificarlo en CPA.
7. Ante un fallo, guardar el motivo en D y `REINTENTAR` en H. Ante éxito confirmado, guardar una nota o dejar D vacío y escribir `ACTUALIZADA A 910` en H.

No escribir el error en otras columnas; no incluir contraseñas en mensajes de error. No deducir trabajo pendiente solo del comentario: **el estado gobierna los reintentos**.

## Concurrencia y preservación

Ejecutar una sola instancia del bot para esta cola. No ordenar físicamente, borrar ni insertar filas intermedias mientras trabaja. Para inspección, usar vistas de filtro sin reordenar la fuente compartida. El servidor únicamente agrega filas y no reescribe D/H de trabajos habilitados durante una sincronización normal.

Si el bot escribe un resultado mientras el servidor sincroniza, ese resultado se leerá en el siguiente ciclo y se conservará en el respaldo. Una confirmación guardada no puede volver a pendiente; si se cambia por error, el servidor restablece ACTUALIZADA A 910. Si se elimina una fila cuyo resultado ya se había sincronizado, se repone con el estado conservado. Si se borra antes de sincronizar el resultado, ese resultado no puede recuperarse: por eso el contrato exige conservar las filas.

Un envío al Sheet cuya respuesta se pierde se verifica por ID_REGISTRO antes de volver a agregarlo. Cualquier ID_REGISTRO ajeno o duplicado detiene la sincronización para evitar sobrescribir datos de otra base.

Los ID_CPA asignados no cambian al importar otro maestro. Los bloqueados sí se resuelven cuando el ID aparece. El estado destino de esta integración es fijo en 910.

## Recuperación y cambio de versión

El respaldo técnico v2 incluye `kyoodo_jobs`, sus secuencias y resultados. La recuperación lee primero los resultados que sigan en la hoja antes de publicar nuevas filas. Los respaldos v1 se pueden recuperar; sus reportes se convierten en trabajos pendientes o bloqueados según el ID disponible. Si ya hubo procesamiento fuera de esta cola, revisar CPA antes de ejecutar esa migración.

No ejecutar el sincronizador antiguo junto al nuevo: el antiguo regenera Kyoodo y puede borrar sus resultados. Actualizar y reiniciar la única instancia central.
