# Sonora

Aplicación local para organizar audio de fuentes y contenidos cuya descarga y conversión estén autorizadas. La interfaz web se sirve desde el propio equipo y muestra el estado de cada pista.

## Alcance y permisos

La primera versión admite **elementos de audio de Internet Archive** (`https://archive.org/details/identificador`) solo cuando los metadatos declaran una licencia CC0, Public Domain Mark, CC BY o CC BY-SA admitida. CC BY-NC, CC BY-ND, licencias ausentes y cualquier fuente distinta se rechazan. La presencia de un elemento en un servicio no basta por sí sola para demostrar que se puede descargar: comprueba los derechos, la licencia concreta y las condiciones del servicio antes de iniciar un trabajo.

No se descargan playlists arbitrarias ni se extrae audio de YouTube. Sonora no ofrece un selector de rutas del sistema. Convierte a MP3 con ffmpeg; la tasa seleccionada no puede mejorar la calidad de origen. Se conserva información de título, autor, fuente y licencia en los metadatos del archivo para facilitar la atribución.

## Ejecutar en Windows desde el código

Requiere Python 3.10+ y ffmpeg disponible en `PATH`:

```powershell
python app.py
```

Sonora abre el navegador en `http://127.0.0.1:8765` y crea `musica` junto a `app.py`. Si Windows no permite escribir allí, muestra una explicación antes de arrancar.

## Crear el ejecutable portátil

En Windows, instala Python 3.10+ y proporciona un `ffmpeg.exe` obtenido de una fuente de confianza:

```powershell
.\scripts\build-windows.ps1 -FfmpegPath "C:\ruta\a\ffmpeg.exe"
```

Distribuye juntos `dist\Sonora.exe` y `dist\ffmpeg.exe` (además de las DLL que el build de ffmpeg necesite; el script copia las DLL contiguas). Usa un build de ffmpeg para Windows de una fuente de confianza y respeta sus términos de distribución. No se necesita instalador ni una instalación de Python en el equipo de destino. Al ejecutar Sonora, se abrirá el navegador y la carpeta de salida será `dist\musica`, junto al ejecutable. La carpeta se crea al iniciar; si no es escribible, se informa del problema.

## Raspberry Pi 4/5 (Raspberry Pi OS de 64 bits)

Instala Docker Engine y el complemento Docker Compose. La imagen `python:3.12-slim` y ffmpeg se construyen explícitamente como Linux ARM64. La carpeta persistente es `./musica` del proyecto, montada en `/music` dentro del contenedor. No se puede cambiar desde la interfaz. Antes de iniciar, crea `musica` con el usuario propietario de los archivos y configura el UID/GID que devuelve `id -u` e `id -g` en un `.env` junto a `compose.yaml`:

```sh
mkdir -p musica
printf 'SONORA_UID=%s\nSONORA_GID=%s\n' "$(id -u)" "$(id -g)" > .env
docker compose build
docker compose up -d
```

Abre `http://IP-DE-LA-PI:8765` desde un equipo de la red doméstica. No configures reenvío de puertos del router ni expongas el servicio a Internet: esta versión no incluye autenticación. Para cambiar el directorio persistente, modifica el volumen de `compose.yaml` antes de iniciar.

## Uso y estados

1. Pega un enlace de elemento admitido, elige 128, 192, 256 o 320 kbps e inicia el trabajo.
2. Sonora valida el dominio, consulta la licencia y descubre archivos de audio. Un permiso ausente o no admitido detiene el trabajo con un mensaje visible.
3. Las pistas se convierten en una subcarpeta con el título del elemento dentro de `musica`. Si ya existe el MP3 de destino, se omite; nunca se sobrescribe.
4. La interfaz distingue pistas procesadas, omitidas y fallidas, con el mensaje concreto de cada error.

En contenedor, ffmpeg ya está incluido. Para ejecutar desde código debe estar instalado localmente o indicarse con `FFMPEG_PATH`. Sonora requiere conexión a Internet para consultar metadatos y obtener archivos de Internet Archive.

## Pruebas

```powershell
python -m unittest discover -s tests -v
```
