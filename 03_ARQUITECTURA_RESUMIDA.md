# Arquitectura resumida

Navegador
  → FastAPI
  → SQLite (verdad persistente)
  → Redis/RQ (cola)
  → Worker FFmpeg
      → ffprobe duración
      → original 0:corte
      → comercial corte:fin
      → MP4 1280x720
      → ffprobe validación
  → Dispatcher canales
      → ventana móvil 7/24h por canal
  → YouTube Worker
      → OAuth por canal
      → upload reanudable
      → videoId/URL
  → cleanup seguro

Archivos permanentes mínimos:
- DB
- assets: fondos y audio comercial
- tokens/secretos seguros
- logs esenciales

Archivos temporales:
- MP3/WAV originales subidos
- MP4 generados
- intermedios

Borrado: sólo después de publicación confirmada + retención configurable.
