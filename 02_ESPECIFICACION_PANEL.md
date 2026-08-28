# Especificación del panel existente

Archivo visual original conocido: `djgabo-demo-engine (1).html`.

## Elementos que deben conservarse
- Tema oscuro, consola de producción.
- Encabezado `DJGABO ENGINE`.
- Métricas clicables: cola, procesando, publicados, errores.
- Canales visibles en la parte superior con medidor de cupo de 7 segmentos.
- Producción e Historial.
- Buscador.
- Dropzone MP3/WAV, múltiples archivos.
- Parseo visual ARTISTA - TÍTULO y revisión manual si falla.
- Estados con barra de progreso.
- Canal asignado por fila.
- Resultado y acción por fila.
- Drawer/detalle de canal y de errores.
- Configuración por canal con imagen de fondo propia y fallback.
- Pausar/reanudar/reintentar/quitar trabajos.

## Cambios necesarios
1. Eliminar `Duración del demo (segundos) = 90`.
2. Añadir `Punto de corte de pista original` en mm:ss y/o segundos.
3. Añadir carga y previsualización del `Audio comercial predeterminado`.
4. Mostrar duración detectada de cada pista.
5. Mostrar `Original hasta 01:20 → Comercial hasta 03:00` o equivalente al abrir detalle.
6. El panel debe decir `servidor`/producción, no depender de `C:\DJGABO\...`.
7. Al publicar, mostrar URL YouTube y botón copiar.
8. Historial debe persistir en DB y poder buscar todos los videos subidos desde el inicio del sistema.
9. Poder cambiar privacidad de un video publicado a public/unlisted/private desde el panel.
10. Poder eliminar un video de YouTube desde el panel, con confirmación fuerte y auditoría.
11. La UI no debe contener datos ficticios/seed en modo producción.
12. Usar backend real para medidores de canales y próximo slot.

## Estados UI sugeridos
PREPARED → QUEUED → RENDERING → VALIDATING → MP4_READY → WAITING_SLOT → UPLOADING_YOUTUBE → VERIFYING → CLEANUP → PUBLISHED.

Estados de excepción: REVIEW_REQUIRED, PAUSED, RENDER_ERROR, UPLOAD_ERROR.
