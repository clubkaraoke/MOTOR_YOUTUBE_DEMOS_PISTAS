# PROMPT MAESTRO — DJGABO YOUTUBE DEMO ENGINE V2

Actúa como ingeniero senior full-stack/DevOps. Debes construir un proyecto funcional, compacto, robusto y mantenible llamado **DJGABO YouTube Demo Engine V2**.

## 1. Meta del producto
El usuario carga de forma masiva archivos MP3/WAV desde un panel web. Cada audio representa una pista musical completa. El video final debe conservar la duración total del audio original, pero la pista musical original sólo puede escucharse hasta un punto de corte configurable. Desde ese punto hasta el final, el audio original debe quedar completamente ausente y ser sustituido por un audio comercial predeterminado.

Ejemplo obligatorio:
- audio original: 03:00
- corte: 01:20
- video final: 03:00
- 00:00–01:20 = audio original
- 01:20–03:00 = audio comercial
- jamás mezclar el original por debajo del comercial después del corte; debe desaparecer.

La imagen de fondo permanece durante toda la duración del video. Salida principal: MP4 1280×720.

## 2. Arquitectura obligatoria
Usar:
- Python 3.12+
- FastAPI
- FFmpeg y ffprobe oficiales por CLI directa
- Redis
- RQ (Redis Queue)
- SQLite inicialmente, con SQLAlchemy
- APScheduler 3.x o un dispatcher persistente equivalente para revisar slots
- google-api-python-client + google-auth para YouTube
- Docker Compose
- Frontend HTML/CSS/JS sin framework pesado, reutilizando el diseño visual existente si está presente

Servicios Docker mínimos:
- web
- worker
- redis

No usar Kubernetes, RabbitMQ, Celery ni una SPA pesada salvo que exista una razón técnica imprescindible. Mantener el producto pequeño.

## 3. Panel visual
Existe una maqueta previa llamada `djgabo-demo-engine (1).html`, con diseño oscuro tipo consola de producción. Si está disponible, NO rediseñarla desde cero.

Conservar como concepto:
- encabezado DJGABO ENGINE
- métricas: En cola / Procesando / Publicados / Errores
- chips de canales C1, C2, C3, C4 con medidor 0–7
- tabs Producción / Historial
- buscador por artista, título, canal y estado
- dropzone para muchos MP3/WAV
- filas con Canción / Estado / Canal / Resultado / Acción
- botón Iniciar producción
- panel Configuración
- fondo predeterminado por canal y fondo de respaldo
- detalle de errores, reintento, pausa y reanudación

Cambios obligatorios frente a la maqueta anterior:
- quitar concepto “Duración del demo = 90 segundos”
- video final dura lo mismo que la pista original
- agregar `Punto de corte de pista original`, default configurable, ejemplo 80 segundos
- agregar `Audio comercial predeterminado`
- agregar transición configurable (default 0.25 s, máximo razonable 1 s)
- cambiar rutas Windows por almacenamiento del servidor
- mostrar URL de YouTube una vez publicado, con botón Copiar
- permitir cambiar privacidad posterior desde el panel cuando la API lo permita: public / unlisted / private
- permitir solicitar borrado del video desde el panel mediante YouTube API, con confirmación

## 4. Ingesta
Aceptar carga múltiple de MP3 y WAV.

Al ingresar un archivo:
- crear job UUID
- guardar temporalmente el original
- obtener duración real con ffprobe
- parsear nombre `ARTISTA - TÍTULO.ext`
- si no puede separar artista/título, estado `REVIEW_REQUIRED`
- permitir editar artista y título antes de iniciar
- calcular checksum SHA-256 para prevenir duplicados accidentales

No bloquear el navegador mientras se suben varios audios. Mostrar progreso de subida.

## 5. Motor audiovisual
Crear una función aislada y testeable equivalente a:
`create_demo_video(original_audio, background_image, commercial_audio, cut_seconds, output_path)`

Reglas:
1. Obtener duración D con ffprobe.
2. Validar: 0 < cut_seconds < D.
3. Original: [0, cut_seconds].
4. Comercial: [cut_seconds, D].
5. Si comercial dura menos, repetir/loop hasta cubrir el tiempo restante.
6. Si dura más, recortar exactamente a D-cut_seconds.
7. Aplicar micro fade/crossfade sólo para evitar clic audible; NUNCA permitir que el original siga sonando después del límite de protección definido.
8. Imagen fija durante D.
9. MP4 1280×720.
10. H.264 + yuv420p.
11. AAC, 48 kHz, bitrate objetivo 256 kbps.
12. `+faststart` para compatibilidad.
13. Optimizar para imagen estática; no usar parámetros innecesariamente lentos.

Seguridad crítica:
- si cut_seconds >= D: no renderizar/publicar; `REVIEW_REQUIRED`
- si no existe audio comercial: no publicar
- si no existe imagen del canal: usar fallback
- si tampoco existe fallback: no publicar

Después del render ejecutar ffprobe y validar:
- MP4 legible
- stream de video presente
- stream de audio presente
- resolución 1280x720
- duración próxima a D (tolerancia técnica pequeña)
- codecs esperados o compatibles

Sólo marcar `MP4_READY` si pasa validación.

## 6. Estados persistentes
Implementar al menos:
- PREPARED
- REVIEW_REQUIRED
- QUEUED
- UPLOADING_SOURCE
- RENDERING
- VALIDATING
- MP4_READY
- WAITING_SLOT
- UPLOADING_YOUTUBE
- VERIFYING
- CLEANUP
- PUBLISHED
- PAUSED
- RENDER_ERROR
- UPLOAD_ERROR

Los jobs no pueden existir sólo en RAM.

## 7. Base de datos
Tabla jobs mínima:
- id UUID
- filename_original
- artist
- title
- sha256
- original_path
- rendered_path
- original_duration_seconds
- cut_seconds
- channel_id nullable
- privacy_status
- status
- progress
- retry_count
- error_code
- error_message
- youtube_video_id
- youtube_url
- created_at
- updated_at
- published_at
- cleanup_at

Tabla channels:
- id
- display_name
- youtube_channel_id
- enabled
- oauth_status
- encrypted/secure token reference
- background_image_path
- max_uploads_24h default 7
- created_at / updated_at

Tabla channel_publications o equivalente para auditar ventana móvil de 24 h.

## 8. Regla 7 videos / 24 horas / canal
Esta es una regla interna del producto y debe ser exacta.

Antes de cada subida:
- contar publicaciones `PUBLISHED` de ese canal con published_at > now - 24h
- si count < 7, hay slot
- si count == 7, no subir y calcular el instante exacto del próximo slot = publicación más antigua de esas 7 + 24h
- buscar otro canal habilitado con cupo
- si todos están llenos: `WAITING_SLOT`

Asignación recomendada: canal habilitado con menor ocupación dentro de las últimas 24 h, respetando orden estable para evitar carreras.

La regla debe sobrevivir reinicios; jamás usar un contador exclusivamente en memoria.

## 9. YouTube
Crear dos modos:
- `YOUTUBE_MODE=mock` para desarrollo/pruebas sin credenciales
- `YOUTUBE_MODE=real` para producción

Modo real:
- OAuth 2.0 por canal
- refresh token persistente y seguro
- uploads reanudables/chunked
- exponential backoff para errores temporales, 429 y 5xx
- no repetir una publicación ya confirmada
- título default: `{artist} - {title}`
- descripción configurable global
- privacidad default configurable
- guardar videoId y URL

Idempotencia:
Evitar que un reinicio exactamente después de subir genere duplicado. Persistir transición/identificador de operación y verificar antes de reintentar cuando sea posible.

## 10. Limpieza automática
El servidor es almacenamiento TEMPORAL, no archivo permanente.

Nunca borrar original o MP4 si la subida falló.

Tras confirmar videoId y registrar `PUBLISHED`:
- pasar a CLEANUP
- eliminar audio original
- eliminar MP4
- eliminar archivos intermedios
- conservar DB, logs esenciales, videoId y URL

Agregar una retención configurable, default 60 minutos después de publicación, antes del borrado físico. Un job fallido puede conservarse configurable, por ejemplo 7 días, con proceso de limpieza posterior.

## 11. Reanudación ante reinicio
Al iniciar servicios:
- revisar jobs interrumpidos
- si existe MP4, validarlo con ffprobe
- si es válido, continuar desde MP4_READY/WAITING_SLOT
- si no existe o está corrupto, volver a renderizar
- si estado era UPLOADING_YOUTUBE, resolver con estrategia idempotente antes de volver a subir

No perder la cola al reiniciar Docker o el servidor.

## 12. Concurrencia y disco
Inicialmente:
- 1 render FFmpeg simultáneo
- 1 upload YouTube simultáneo
- configurables por env

No renderizar 40 MP4 de golpe sin necesidad. Implementar `READY_BUFFER`, por ejemplo 3–5 videos listos por delante, para limitar uso de disco.

Aceptar 20, 30, 40+ audios en cola; eso no significa mantener todos los MP4 pre-renderizados.

## 13. Seguridad
- secretos sólo en variables de entorno / archivos de secretos fuera del repo
- `.env` en `.gitignore`
- nunca loguear refresh tokens, client_secret o access tokens
- validar extensiones y MIME razonablemente
- nombres internos por UUID, no confiar en nombres de archivo del usuario
- proteger rutas contra path traversal
- límites configurables de tamaño por archivo y cuota de almacenamiento temporal
- endpoint de healthcheck

## 14. API sugerida
No es obligatorio usar exactamente estos paths, pero cubrir estas capacidades:
- POST /api/jobs/upload
- GET /api/jobs
- GET /api/jobs/{id}
- PATCH /api/jobs/{id}
- POST /api/jobs/{id}/retry
- POST /api/jobs/{id}/pause
- POST /api/jobs/{id}/resume
- DELETE /api/jobs/{id} sólo si no está publicando
- GET /api/channels
- PATCH /api/channels/{id}
- POST /api/channels/{id}/oauth/start
- GET /api/channels/{id}/oauth/callback
- POST /api/settings/background
- POST /api/settings/commercial-audio
- GET /api/history
- GET /health

## 15. Logs y observabilidad
- log estructurado por job_id
- errores claros para UI
- progreso real o estimado de FFmpeg y upload
- distinguir errores recuperables y definitivos
- reintento manual y automático

## 16. Docker / almacenamiento
Estructura recomendada:
```
app/
  api/
  core/
  models/
  services/
    media.py
    youtube.py
    scheduler.py
    cleanup.py
  workers/
  static/
  templates/
data/
  incoming/
  processing/
  ready/
  assets/
  failed/
  db/
tests/
docker-compose.yml
Dockerfile
requirements.txt
.env.example
README.md
```

Usar volúmenes persistentes para DB, assets y temporales que deban sobrevivir reinicios.

## 17. Referencia externa
Estudiar conceptualmente:
https://github.com/151henry151/archive-to-video

Útiles como ideas:
- ffprobe antes/después
- reutilización de MP4 válido
- OAuth refresh
- resumable upload
- cleanup después de éxito
- UI de progreso

NO copiar su código literalmente. Implementar de cero; dicho proyecto está bajo GPLv3.

## 18. Criterio de finalización
NO declares el proyecto terminado por mostrar el panel.
Está terminado sólo cuando:
- corre con `docker compose up`
- upload múltiple real funciona localmente
- FFmpeg genera el MP4 correcto
- prueba de protección de audio demuestra que el original ya no existe después del corte
- Redis/RQ conserva la cola
- SQLite conserva estados
- reinicio recupera trabajos
- mock YouTube publica y genera URL simulada
- límite 7/24h está testeado
- cleanup está testeado
- UI consume backend real, no arrays simulados
- suite automática pasa
- README explica instalación y cambio mock→real

## 19. Entrega de Codex
Entregar código completo, no snippets ni parches. Al finalizar mostrar:
1. árbol de archivos
2. comandos exactos para levantar el proyecto
3. resultados de tests
4. credenciales/configuraciones que aún debe aportar el usuario
5. riesgos o TODOs reales, si existen

No modificar funciones ya validadas por capricho. Priorizar robustez, simplicidad y trazabilidad.
