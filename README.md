# DJGABO YouTube Demo Engine V2

Motor web persistente para convertir lotes MP3/WAV en demos MP4 1280×720, con audio protegido, cover automático, QR único, almacenamiento temporal en Google Drive y publicación distribuida entre cuatro canales de YouTube.

## Probar el panel local

En Windows, ejecute `INICIAR_DJGABO.bat` y mantenga abierta la consola. El navegador abre:

`http://127.0.0.1:8088`

También puede iniciarlo desde PowerShell:

```powershell
python -m pip install -r requirements.txt
python run_local.py
```

Para Docker:

```bash
cp .env.example .env
docker compose up --build
```

Docker publica el panel en `http://localhost:8088`. La interfaz nunca debe abrirse como archivo `file:///...`; CSS, JavaScript y API se sirven a través de FastAPI.

## Flujo automático

1. El usuario arrastra uno o varios MP3/WAV.
2. FastAPI valida duración y sube cada original a la carpeta temporal de Google Drive.
3. El motor busca el cover en `01_CEREBRO2` usando artista, título y nombre original con coincidencia tolerante.
4. Si el cover falta o su URL no devuelve una imagen válida, el job permanece en `WAITING_COVER`. Se reintenta cada 5 minutos o con **Actualizar covers ahora**.
5. Cuando hay cover y cupo 7/24 h, se descarga solamente el audio que va a procesarse.
6. Pillow crea `frame_final.png` con fondo, cover, artista, título, QR y WhatsApp.
7. Un bloqueo global permite como máximo un FFmpeg activo. El audio original termina en el corte y desde allí sólo suena el comercial.
8. Se valida el MP4, se publica en YouTube y se guarda el `videoId`.
9. El QR queda asociado a la URL exacta del video. Sólo entonces se eliminan el audio de Drive y los temporales locales; el historial y el token QR permanecen.

No hay aprobación manual de cover, título, QR ni publicación. Los errores reales quedan persistidos para reintento.

## Configuración inicial

Desde **Configuración** puede editar el corte de protección (80 s por defecto) y la transición. Cada canal admite dos imágenes 1280×720: la primera se muestra durante 20 segundos y la segunda, con el QR, permanece hasta el final.

El nombre recomendado es `ARTISTA - TÍTULO KARAOKE (Coro).wav`. `(Coro)` se convierte en `+ COROS`; `KARAOKE` se conserva si el título cabe y se elimina primero cuando hace falta respetar el límite de 100 caracteres.

## Google Drive y catálogo de covers

Los identificadores ya están configurados:

- carpeta temporal Drive: `14GwUYaJRPw7nV5UlyS_XV9EbAWQqpIok`;
- spreadsheet: `14ytnhSOmcsh18hIQWX1YK0n7jGyQtvbr6_yuMCRlv14`;
- pestaña: `01_CEREBRO2`;
- columnas: `NOMBRE ARTISTA`, `TITULO CANCION`, `COVER`, `ARCHIVO_ORIGINAL`.

Para una prueba real, copie `.env.example` a `.env` y configure:

```dotenv
GOOGLE_MODE=real
GOOGLE_CREDENTIALS_FILE=/ruta/segura/credenciales-google.json
PUBLIC_BASE_URL=https://dominio-publico.example
WHATSAPP_NUMBER=51999999999
```

La identidad de esas credenciales debe tener edición en la carpeta temporal y lectura del spreadsheet. `COVER` acepta cualquier URL HTTP/HTTPS que realmente devuelva JPG, PNG o WEBP, incluidos Cloudinary e Imgur. En Docker, monte el JSON como secreto o archivo de sólo lectura; no lo copie al repositorio.

## YouTube real

Configure además:

```dotenv
YOUTUBE_MODE=real
YOUTUBE_CLIENT_ID=...
YOUTUBE_CLIENT_SECRET=...
OAUTH_REDIRECT_BASE_URL=https://dominio-publico.example
TOKEN_ENCRYPTION_KEY=...
```

Como alternativa más segura para el cliente OAuth, descargue el JSON de tipo **Aplicación web** en
`data/google-auth/youtube-oauth-client.json`. El motor lo detecta automáticamente y genera una clave
Fernet local en `data/google-auth/youtube-token.key`; ambos archivos están excluidos de Git.

Se requiere YouTube Data API v3, OAuth Client tipo Web, callback HTTPS y autorización independiente desde **OAuth** en C1–C4. Genere la clave Fernet con:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`PUBLIC_BASE_URL` debe ser accesible desde el teléfono que escaneará el QR. `127.0.0.1` sólo sirve en la misma máquina.

## Variables principales

| Variable | Predeterminado | Uso |
|---|---:|---|
| `GOOGLE_MODE` | `mock` | `mock` local o `real` para Drive/Sheets |
| `YOUTUBE_MODE` | `mock` | publicación simulada o real |
| `RENDER_CONCURRENCY` | `1` | máximo previsto de renders |
| `COVER_CACHE_SECONDS` | `300` | actualización del catálogo |
| `COVER_MATCH_THRESHOLD` | `78` | umbral fuzzy |
| `DEFAULT_CUT_SECONDS` | `80` | corte del audio original |
| `AUDIO_CROSSFADE_SECONDS` | `0.25` | transición, máximo 1 s |
| `MAX_UPLOADS_PER_CHANNEL_24H` | `7` | cupo móvil por canal |
| `PUBLIC_BASE_URL` | `http://127.0.0.1:8088` | base del QR dinámico |
| `WHATSAPP_NUMBER` | `51921675846` | número internacional sin signos |

## Persistencia y reinicios

SQLite conserva jobs, referencias Drive, covers, estados, canales, publicaciones y QR. Redis usa AOF en Docker. Al reiniciar se recuperan subidas a Drive interrumpidas, trabajos descargando/renderizando y MP4 válidos; una publicación con `videoId` no se vuelve a crear.

El worker Docker es único y `data/processing/ffmpeg.lock` impide más de un FFmpeg incluso si accidentalmente existen varios workers.

## API útil

- `POST /api/jobs/upload`, `GET /api/jobs`, `PATCH /api/jobs/{id}`
- `POST /api/jobs/{id}/retry`, `/pause`, `/resume`
- `POST /api/covers/refresh`
- `GET /q/{token}`
- `GET/PATCH /api/channels`, fondos y OAuth por canal
- `POST /api/settings/commercial-audio`, `/api/settings/background`
- `GET /health`, `GET /docs`

## Pruebas

```bash
python -m pytest -q -p no:cacheprovider tests
```

La suite prueba FFmpeg real de 180 s, corte a 80 s, codecs/resolución/duración, ausencia del original después del corte, loop/recorte comercial, persistencia, 7/24 h, publicación idempotente, limpieza segura, matching de cover, título con Coros, frame y QR únicos, y ciclo temporal de Drive mock.

## Despliegue

En Linux/OVH use Docker Compose, proxy HTTPS, backups de SQLite y secretos fuera del repositorio. No exponga Redis. Para escalar a múltiples workers conviene PostgreSQL y bloqueos transaccionales, aunque el bloqueo global de FFmpeg seguirá limitando el render.
