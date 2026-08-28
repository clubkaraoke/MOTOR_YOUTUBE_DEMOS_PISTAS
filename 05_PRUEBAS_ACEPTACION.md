# Pruebas de aceptación obligatorias

## A. Motor de audio/video
- Audio original 180 s, corte 80 s, comercial cualquiera.
- MP4 final ≈ 180 s.
- Resolución 1280x720.
- De 0–80 s se oye original.
- Después de 80 s el original no existe; sólo comercial.
- Comercial corto hace loop hasta el final.
- Comercial largo se recorta.
- Corte >= duración: REVIEW_REQUIRED, jamás publicar.
- Falta comercial: bloquear publicación.
- Falta fondo canal + existe fallback: usar fallback.
- Falta ambos fondos: bloquear.

## B. Persistencia
- Crear 20 jobs.
- Reiniciar web/worker/redis según escenario controlado.
- Confirmar que jobs y estados no desaparecen.
- MP4 válido ya generado no debe recodificarse.

## C. 7/24h
- Simular C1 con 7 publicaciones dentro de 24 h: no permitir octava.
- C2 con 6: permitir una.
- Si todos llenos: WAITING_SLOT.
- Confirmar cálculo del próximo slot por publicación más antigua + 24 h.
- Reiniciar servidor y verificar que el cupo sigue correcto.

## D. YouTube mock
- Simular upload exitoso: generar videoId y URL mock.
- Cambiar estado a PUBLISHED.
- Programar cleanup.
- Simular error temporal y comprobar reintento sin duplicar.

## E. Cleanup
- Antes de PUBLISHED: nunca borrar original/MP4.
- Después de PUBLISHED + retención: borrar original, MP4 e intermedios.
- DB/historial/URL deben permanecer.

## F. UI
- Subida múltiple con progreso.
- Edición artista/título.
- Buscador y filtros.
- Medidores reales por canal.
- Próximo slot visible cuando canal está lleno.
- URL publicada copiable.
- Pausar/reanudar/reintentar.
- Historial persistente.
- No mostrar datos seed/ficticios en producción.

## G. Comando final
Todo debe poder levantarse con:
`docker compose up --build`

y disponer de `GET /health` satisfactorio.
