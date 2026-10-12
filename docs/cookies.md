# Cookies de YouTube

Si yt2pod corre en un VPS, lo más probable es que YouTube bloquee las descargas con
**«Sign in to confirm you're not a bot»** (en la app: *YouTube pide verificar que no eres un bot*).
YouTube trata como bots a casi todas las IPs de centros de datos. La solución es darle a yt2pod las
cookies de una sesión de YouTube: así descarga como si fueras tú desde tu navegador.

En un servidor en casa normalmente no hace falta.

## Antes de empezar

- **Usa una cuenta de Google secundaria**, no la principal. Las cookies dan acceso a esa cuenta y
  YouTube puede limitar o bloquear cuentas que descargan mucho. Una cuenta nueva y vacía sirve.
- Hazlo **en un ordenador**. Desde el móvil no se pueden exportar cookies con comodidad.
- No hace falta YouTube Premium.

## Paso a paso (Chrome, Edge o Brave)

1. Instala la extensión **[Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)**.
   Es de código abierto y no envía las cookies a ningún sitio. Evita la antigua «Get cookies.txt»
   (sin *LOCALLY*): se retiró por enviar datos fuera.
2. Permítela en incógnito: menú de extensiones → *Get cookies.txt LOCALLY* → *Detalles* →
   activa **Permitir en modo incógnito**.
3. Abre una **ventana de incógnito** y entra en <https://www.youtube.com> con la cuenta secundaria.
4. En esa misma pestaña, ve a <https://www.youtube.com/robots.txt>. Así la sesión queda en una página
   que no vuelve a tocar las cookies.
5. Pulsa el icono de la extensión → formato **Netscape** → **Export**. Se descarga un archivo como
   `www.youtube.com_cookies.txt`.
6. **Cierra la ventana de incógnito sin cerrar sesión.** Si cierras sesión, las cookies dejan de valer al
   momento. Y si sigues navegando por YouTube en esa ventana, YouTube las renueva y las del archivo caducan antes.
7. En yt2pod: **Ajustes → Acceso a YouTube → Subir cookies.txt** y elige el archivo. Las descargas que
   estaban bloqueadas se reintentan solas.

## Firefox

Igual que arriba, con la extensión **[cookies.txt](https://addons.mozilla.org/firefox/addon/cookies-txt/)**
en una **ventana privada** (en la ficha de la extensión: *Ejecutar en ventanas privadas → Permitir*).
Exporta solo las cookies del sitio actual.

## Con la terminal (alternativa)

Si tienes yt-dlp en tu ordenador y la cuenta abierta en Firefox:

```bash
yt-dlp --cookies-from-browser firefox --cookies cookies.txt --skip-download "https://www.youtube.com/watch?v=jNQXAC9IVRw"
```

Eso crea `cookies.txt` con tu sesión de Firefox. Fuera de incógnito, el navegador sigue usando la
sesión y YouTube rota las cookies antes. Por eso es preferible el método de la ventana privada.

## Sin la app

El archivo vive en `<YT2POD_DATA_DIR>/cookies.txt`. En Docker o Dokploy es `/data/cookies.txt` dentro
del volumen. También puedes copiarlo ahí a mano o apuntar a otra ruta con `YT2POD_COOKIES_FILE`.
yt2pod lo lee en cada descarga, así que no hace falta reiniciar.

## ¿Cuánto duran?

No hay una fecha fija. Si las exportas como se explica arriba y nadie vuelve a usar esa sesión, suelen
aguantar **semanas o meses**. Caducan antes si:

- cierras sesión en esa cuenta,
- sigues navegando por YouTube con la misma sesión,
- cambias la contraseña de la cuenta o Google la marca como sospechosa.

Cuando dejen de valer, las descargas vuelven a fallar con el aviso del bot. Repite los pasos y súbelas de nuevo.

## Si aun así falla

- Comprueba que el archivo empieza por `# Netscape HTTP Cookie File` y tiene líneas de `.youtube.com`.
  yt2pod rechaza el archivo si no las encuentra.
- Actualiza yt-dlp: la imagen Docker lo hace sola en cada arranque (*Deploy* en Dokploy).
- Algunas IPs de VPS están tan marcadas que YouTube bloquea incluso con cookies. Entonces lo más fiable es
  correr yt2pod en casa (una Raspberry Pi o un PC encendido) con Tailscale.

## Seguridad

Las cookies son como una sesión abierta de esa cuenta. yt2pod las guarda solo en tu servidor, con
permisos `600`. Solo la cuenta de administrador puede subirlas o borrarlas, y la app nunca las muestra.
Borrarlas desde *Ajustes* elimina el archivo.
