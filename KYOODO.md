# Kyoodo · servicio Google por láser

La cola sigue en el Sheet. Cada PC ejecuta su Kyoodo, pero las lecturas/reservas y los resultados pasan por la API HTTPS. No distribuir el JSON de Google ni dar acceso directo al Sheet a operadores.

## Autenticación

- `POST /login` con `code` y `password` devuelve `token` y `user`.
- En primer acceso, usar `/password` con `old` y `new`. El usuario debe haber sido asignado a una láser por supervisión.
- En las siguientes solicitudes enviar `Authorization: Bearer TOKEN`.
- El código y clave son los de acceso a Corte. El correo/clave de CPA se obtienen solo al reservar un trabajo propio; no son las credenciales de acceso a la aplicación.

## Tomar el próximo trabajo

`POST /kyoodo/next` con un `request_id` UUID nuevo para esa solicitud. Si se pierde la respuesta, repetir con **el mismo UUID** hasta obtener confirmación. Esto devuelve la misma reserva, no otra orden.

Respuesta con trabajo:

```json
{
  "job": {
    "# DE ORDEN": "SO-0000000",
    "LINEA": "A",
    "ID_REGISTRO": "uuid-del-reporte",
    "ID_CPA": "id-original-cpa",
    "ESTADO": "EN PROCESO",
    "STATUS_DESTINO": 910,
    "LASER_ID": "LASER_01",
    "CLAIM_ID": "reserva-aleatoria"
  },
  "account": {"email": "cuenta-de-esta-laser", "password": "clave-cpa"}
}
```

Solo se reservan filas propias habilitadas, comenzando por la SECUENCIA más baja. No se usa el número de orden ni la fecha de producción para determinar antigüedad. Si no hay trabajo, `job` es null. Si ya existe una reserva vigente para esa láser y otra solicitud, devuelve `busy: true`; no iniciar otro proceso.

La reserva dura 600 segundos. Si un ejecutor desaparece, otro puede retomarla después; se genera un CLAIM_ID nuevo y el anterior ya no permite confirmar. Si una respuesta de CPA se perdió, leer el estado real antes de volver a modificarlo.

## Ejecutar y confirmar

1. Abrir sesión en CPA con la cuenta que devuelve la API para esa láser.
2. Buscar ID_CPA en la orden indicada y verificar que corresponda a su línea.
3. Si el estado ya es 910, confirmar sin repetir la actualización. Si no, actualizar y comprobar el resultado en CPA.
4. `POST /kyoodo/result` con:

```json
{
  "request_id": "uuid-del-reporte",
  "claim_id": "reserva-aleatoria",
  "state": "ACTUALIZADA A 910",
  "comment": "Confirmado en CPA"
}
```

En caso de fallo, enviar `state: "REINTENTAR"` y el error en `comment`. No incluir contraseñas ni tokens en el comentario. Repetir la misma confirmación si su respuesta se pierde: una confirmación completada es idempotente.

Un error no se deduce solamente del comentario: **ESTADO gobierna el reintento**. Dar como máximo un intento por registro en cada ciclo del bot, y evitar bucles inmediatos sobre el mismo fallo. Cuando no haya trabajo, espaciar las consultas (por ejemplo 60 segundos); aumentar la espera ante 429/503.

## Columnas de Kyoodo

| Columnas | Uso |
| --- | --- |
| A–F | # DE ORDEN, LASER, SECUENCIA, COMENTARIO, LINEA, ID_REGISTRO |
| G–L | ID_CPA, ESTADO, ORDEN_LINEA, FECHA_REGISTRO, STATUS_DESTINO, LASER_ID |
| M–P | CLAIM_ID, CLAIM_EXPIRA, INTENTOS, CLAIM_REQUEST_ID |

Los datos son valores, no fórmulas. El ID_REGISTRO no cambia y el ID_CPA asignado queda congelado. Si el ID falta, el estado es BLOQUEADA SIN ID CPA hasta que aparezca en el maestro. Las filas ACTUALIZADA A 910 se conservan como historial y no se vuelven a reservar.

No borrar ni ordenar físicamente filas. No escribir resultados directamente en celdas: usar la API evita conflictos entre operadores, revisiones del servicio y ejecutores. Los comentarios del operador están en Consolidado, separados del error del bot.

Esta API no navega ni actualiza el CPA por sí sola. El ejecutor RPA debe adaptarse al contrato y probarse con la cuenta de cada láser antes de habilitarlo en producción.
