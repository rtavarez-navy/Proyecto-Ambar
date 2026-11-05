# Publicar Corte en Google, sin PC servidor

El cliente y la API son Python. Google Sheets contiene los datos; Cloud Run ejecuta la API cuando recibe solicitudes. Un bucket privado de Cloud Storage coordina escritores mediante precondiciones de generación. No contiene producción ni credenciales.

## Lo que falta para activar

- Un proyecto de Google Cloud con facturación habilitada y permisos para habilitar APIs, compilar y desplegar Cloud Run, crear una cuenta de servicio y un bucket.
- El enlace privado de tu Sheet de Corte. No se incluye en el repositorio público.
- Compartir el Sheet como editor con la cuenta de servicio que ejecute Cloud Run.
- Una prueba real del servicio y de los clientes antes de apagar definitivamente la versión anterior.

No se necesita descargar un JSON de cuenta de servicio. El servicio utiliza la identidad adjunta de Cloud Run. Cloud Run, compilación, imágenes y Storage pueden generar cargos según uso; revisar la facturación y configurar un presupuesto en el proyecto. Esta guía no promete funcionamiento gratuito.

## Despliegue desde Google Cloud Shell

Cloud Shell evita instalar herramientas en las PC de planta. Clonar el repositorio y entrar a su raíz. Leer los comandos antes de ejecutarlos: habilitan servicios, crean recursos privados y publican una API HTTPS con autenticación de aplicación.

```bash
git clone https://github.com/rtavarez-navy/Proyecto-Ambar.git
cd Proyecto-Ambar
export CORTE_PROJECT='TU_PROYECTO'
export CORTE_BUCKET='NOMBRE_GLOBAL_UNICO_BUCKET_PRIVADO'
export CORTE_REGION='us-east1'
python scripts/configurar_cloud.py
bash scripts/deploy_cloud.sh
```

El asistente genera `cloud.env.yaml` local, ignorado por git. Pide el Sheet, el nombre del bucket, código de supervisor y clave inicial (sin mostrarla). Guarda un hash de esa clave, no la clave en texto. El bucket indicado debe coincidir con CORTE_BUCKET.

El script crea `corte-sk2-runtime@TU_PROYECTO.iam.gserviceaccount.com` y muestra ese correo. Compartir el Sheet con él como editor y continuar. El script asigna acceso a objetos solamente sobre el bucket privado de coordinación; no necesita roles de administrador sobre todo el proyecto para la identidad de ejecución.

La API se publica con `--allow-unauthenticated` porque las PC se autentican por código y clave de Corte, no por IAM de Google. Todas las rutas de datos requieren sesión; solo `/login` y `/health` no requieren token. El límite de instancias y la concurrencia se fijan en uno para moderar carga, y el bloqueo de Storage protege también durante cambios de revisión.

Si Cloud Build solicita roles de compilación, asignarlos únicamente a la identidad de compilación correspondiente en tu proyecto, siguiendo el mensaje de Google. La identidad de ejecución no debe recibir permisos de despliegue.

## Primer uso

1. El script imprime la dirección `https://...run.app`. Comprobar `/health` y luego iniciar sesión desde el cliente: `/health` por sí solo no comprueba los permisos de Sheets.
2. En todas las PC, pegar esa dirección en Corte o distribuir un `CorteSK2.json` con `{"server":"https://TU-SERVICIO.run.app"}` junto al ejecutable.
3. Si no había usuarios, entrar con el supervisor configurado y cambiar su clave. Si se migró `_Estado`, se conservan los códigos y hashes anteriores; usar esas claves.
4. Verificar las 11 láseres, asignaciones, cuentas CPA y maestro. Hacer un reporte de prueba y confirmar que aparece una sola vez en Consolidado y Kyoodo.
5. Probar dos terminales a la vez y reiniciar el servicio: las sesiones y registros deben continuar porque están en Sheets.
6. Probar las reservas de Kyoodo y después integrar el ejecutor real de CPA con KYOODO.md.

No se ha realizado este despliegue automáticamente: hacen falta tu proyecto, sus permisos y configuración de facturación. Los archivos y pruebas del repositorio no sustituyen esta verificación real.

## Migración desde el servidor local

1. Sincronizar y detener la única instancia local. Confirmar que no quedaron reportes pendientes solo en su disco.
2. Conservar un respaldo del archivo SQLite y una copia del Sheet antes del cambio.
3. Publicar la API nueva contra ese mismo Sheet. En su primera inicialización crea `_Usuarios`, `_Sesiones`, `_Accesos` y `_Auditoria`.
4. Si existe `_Estado` y `_Usuarios` todavía no tiene usuarios, importa sus hashes y, si Asignaciones está vacía, sus asignaciones. No modifica Maestro, las fórmulas de CPA DATA ni filas existentes de Consolidado/Kyoodo.
5. No volver a ejecutar el sincronizador antiguo; este reescribe tablas que ahora son la base central. Los accesos del cliente deben usar HTTPS, no localhost.

El respaldo `_Estado` previo queda como archivo histórico. No es la base activa de la nueva versión. Para copias posteriores, respaldar el Sheet completo, incluidas hojas técnicas y reservas, con el servicio sin tráfico.

## Operación y cuotas

El cliente actualiza novedades cada 60 segundos; guardar un reporte trae la nueva vista en la misma respuesta. Las operaciones leen tablas mediante batchGet y agrupan escrituras mediante batchUpdate RAW. Supervisar cuota, latencia y volumen con las 11 láseres antes del uso general; ajustar la cuota del proyecto si es necesario.

Ante una respuesta 503 o pérdida de Internet, un envío puede haber sido aceptado: reintentar con el mismo ID_REGISTRO. El cliente conserva ese ID mientras permanezca abierto. No cerrar un envío pendiente sin revisar el reporte.

## Bloqueo de coordinación

El objeto `locks/corte-ID_DEL_SHEET.json` se crea con `if_generation_match=0` y se elimina usando su generación. Mientras exista, otra escritura devuelve 503 en lugar de sobrescribir datos. **No expira automáticamente**: se prioriza evitar dos escritores sobre continuar tras un proceso interrumpido.

Si un proceso muere con el bloqueo tomado, las lecturas pueden seguir disponibles y las escrituras quedan bloqueadas. Para recuperar: poner el servicio en mantenimiento, detener el tráfico y asegurarse de que no quede ninguna solicitud activa; después eliminar el objeto de bloqueo en Cloud Storage y reabrir el servicio. No eliminarlo por antigüedad mientras pueda haber una escritura activa. No configurar una regla de borrado automático del bucket.

## Variables del servicio

| Variable | Uso |
| --- | --- |
| CORTE_SPREADSHEET_ID | ID privado del Sheet |
| CORTE_LOCK_BUCKET | Bucket privado de coordinación |
| CORTE_SUPERVISOR_CODE | Código para inicialización sin usuarios previos |
| CORTE_SUPERVISOR_HASH | Hash PBKDF2 de su clave inicial |
| PORT | Puerto inyectado por Cloud Run |

No publicar cloud.env.yaml, contraseñas, hashes reales, tokens o el enlace privado del Sheet en el repositorio.
