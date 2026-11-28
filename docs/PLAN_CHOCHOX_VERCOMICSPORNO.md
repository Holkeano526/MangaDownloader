# Plan: chochox.com y vercomicsporno.com

Estado del repo al escribir esto: `main` en `7d93a39` (merge del PR #14), árbol limpio.
El PR #14 revirtió `b7ef098` (capa de cloudscraper) con este motivo textual:

> No resolvía el desafío de Cloudflare de chochox.com ni de vercomicsporno.com, que eran el
> motivo de añadirla, **y el proyecto no esquiva protecciones anti-bot**.

Eso convierte este plan en una decisión de producto antes que en una tarea técnica. Abajo
están las dos vías, con lo que cuesta cada una y lo que implica.

---

## 1. Qué hay hoy (verificado)

| Pieza | Estado |
|---|---|
| `chochox.com`, `vercomicsporno.com` | En `BLOQUEADOS_CLOUDFLARE` (`core/handler.py`): el bot solo avisa |
| Búsqueda | No están en `BUSCADORES` (`core/busqueda.py`) |
| Handlers | No existen en `core/sites/` |
| Política escrita | `HOW_TO_ADD_SITES.md`: «**No esquives protecciones** (Cloudflare, captchas, anti-bot)» |
| Dependencias | `playwright` ya está en `requirements.txt`; Chromium ya está en la imagen base del bot |

Los dos sitios responden `403` + `cf-mitigated: challenge` (desafío gestionado con Turnstile)
a `aiohttp` **y** a `cloudscraper`. No es un fallo de integración: es un tipo de desafío que
cloudscraper no resuelve.

---

## 2. Decisión previa (bloquea el resto)

### Opción A — No saltar la protección (recomendada, cumple la política)

El bot sigue sin descargar de esos dominios, pero deja de ser un callejón sin salida:
cuando alguien pega un enlace de chochox/vercomicsporno, el bot saca el título del slug y
**busca esa misma obra en los 15 sitios compatibles**, ofreciendo los resultados ya listos
para descargar.

- Coste: 2–3 h.
- No toca ninguna protección, no contradice el PR #14, no depende de Chromium.
- Cubre el caso real la mayoría de las veces: el mismo cómic suele estar en `allporncomic`,
  `sexkomix2`, `imhentai` o `hentalk` (se ve en los propios catálogos: «Steven's Desire 2 –
  HarkArt» está en chochox, vercomicsporno y allporncomic).
- Detalle en la sección 7.

### Opción B — Navegador real (lo que pediste; sección 3)

Chromium resuelve el desafío una vez, guarda el perfil y el bot reutiliza esa sesión.

- Coste: 1,5–2 días.
- **Revierte de facto la decisión del PR #14** y las condiciones de Cloudflare prohíben eludir
  sus medidas anti-bot. Si se hace, conviene que sea una decisión explícita del dueño del repo
  (y, si Robert u otros colaboradores mantienen la política, discutirlo antes de mergear).
- Frágil por diseño: Cloudflare cambia el desafío y hay que volver a ajustar.

### Opción C — FlareSolverr (contenedor aparte)

Mismo efecto que B con menos código propio, pero suma un servicio Docker, una dependencia
externa y el mismo conflicto de política. No la recomiendo frente a B.

> **Recomendación:** A ahora, y B solo si el dueño del repo acepta cambiar la política por
> escrito (una línea en `HOW_TO_ADD_SITES.md` y en el README).

---

## 3. Plan B en detalle (solo si se aprueba)

### 3.1 Arquitectura

Máximo aislamiento: **no se toca la capa HTTP común ni los handlers existentes**. Todo lo
nuevo vive en un módulo y dos handlers.

```
core/navegador.py          Chromium persistente + adaptador de peticiones
core/sites/chochox.py      Handler del sitio
core/sites/vercomicsporno.py
core/busqueda.py           +2 buscadores que usan core/navegador.py
```

`core/navegador.py`:

```python
class SesionNavegador:
    """Chromium (Playwright) con perfil persistente para desafíos gestionados de Cloudflare."""

    @classmethod
    async def abrir(cls) -> "SesionNavegador": ...
    async def texto(self, url: str, timeout: float = 45) -> str: ...
    async def bytes(self, url: str, timeout: float = 60) -> bytes: ...
    async def cookies(self, url: str) -> dict[str, str]: ...
    async def cerrar(self) -> None: ...

async def obtener_sesion() -> SesionNavegador:  # singleton perezoso
```

Puntos finos:

1. **`launch_persistent_context`** con perfil en `temp_manga_images/navegador_perfil`
   (configurable con `CF_PERFIL`). El `cf_clearance` sobrevive a reinicios del bot: el desafío
   se resuelve una vez cada varias horas, no en cada descarga.
2. `headless=True`, `--no-sandbox`, `--disable-dev-shm-usage`,
   `--disable-blink-features=AutomationControlled`, `user_agent=config.USER_AGENT`,
   `locale="es-ES"`, viewport 1280×800, e `init_script` que borra `navigator.webdriver`.
3. **Espera del desafío**: `goto(wait_until="domcontentloaded")` y bucle de hasta 45 s
   comprobando que el título ya no sea `Just a moment...` / `Un momento...` / `Attention
   required`. Se registra el tiempo que tarda (métrica útil en el log).
4. **Peticiones y descargas**: se hacen con `context.request` (la pila de red de Chromium,
   con las cookies y el TLS del navegador). Para las imágenes se puede probar antes `aiohttp`
   con la cabecera `Cookie` exportada — es mucho más rápido — y caer al navegador si da 403.
5. **Un lock por host**: dos descargas simultáneas del mismo sitio no deben lanzar dos
   desafíos a la vez.
6. **Cierre por inactividad** a los 10 min (Chromium ocioso son ~250–300 MB).
7. Si Playwright o Chromium no están, el módulo lanza `ErrorNavegador` y los handlers
   muestran el aviso actual.

### 3.2 Fases

#### Fase 0 — Prueba de concepto (medio día)

Antes de escribir handlers, comprobar con un script suelto:

1. ¿Chromium headless pasa el desafío de los dos sitios? ¿En cuánto tiempo?
2. ¿Las cookies exportadas sirven para que `aiohttp` baje una imagen del CDN? (si no, todas
   las descargas van por `context.request`).
3. ¿Cuánto aguanta la sesión? (medir a los 10, 30 y 60 min).

**Criterio de corte:** si el desafío no pasa en headless, B no es viable sin Xvfb/FlareSolverr
y hay que replantear. No se escribe código de producción antes de este resultado.

#### Fase 1 — `core/navegador.py` (1 día)

- Módulo completo con la API de 3.1.
- Singleton, lock por host, cierre por inactividad, logs `[INFO] Cloudflare: desafío resuelto
  en <host> en 12,4 s`.
- Variables: `CF_PERFIL`, `CF_TIMEOUT_DESAFIO` (45 s), `CF_INACTIVIDAD` (600 s).
- `.gitignore`: el perfil.

#### Fase 2 — Handler `chochox.com` (medio día)

- Dominios: `chochox.com`.
- Título: `<h1>` (o `<title>` quitando ` - ChoChoX.com`).
- Imágenes: dentro de `<div class="posts">`, atributo `src` (vienen directas, no `data-src`),
  en orden del DOM, filtrando `/wp-content/uploads/` y descartando banners/logo.
- Descarga: `download_and_make_pdf(urls, clean_filename(titulo), headers={..., "Cookie": ...})`.
- Si el desafío no se resuelve: `[ERROR] chochox.com sigue pidiendo verificación...`.

#### Fase 3 — Handler `vercomicsporno.com` (medio día)

- Dominios: `vercomicsporno.com`.
- Título: `h1.titl`.
- Imágenes: `<div class="posts">`, `alt` = título del cómic; los nombres de archivo llevan
  prefijo aleatorio (`0cgeqwqi-SD01.jpg`), así que **el orden es el del DOM**, nunca el
  alfabético.
- Descartar `bANNER`, `logo`, `Comic-en-proceso`.
- Registrar ambos en `core/sites/__init__.py` y en `HANDLERS` (`core/handler.py`), y quitarlos
  de `BLOQUEADOS_CLOUDFLARE`.

#### Fase 4 — Búsqueda (medio día)

- `_chochox`: `GET https://chochox.com/?s=<q>` (WordPress). Reutiliza `_enlaces()` de
  `core/busqueda.py`; filtrar `/category/`, `/page/`, `/author/`, `/tag/`, `/wp-*`.
- `_vercomicsporno`: `GET https://vercomicsporno.com/comics-porno/?s=<q>`.
- Añadirlos a `BUSCADORES` y a `_IDIOMA_SITIO` (`es` para chochox; vercomicsporno publica
  sobre todo cómics en inglés con ficha en español → decidir con una muestra).
- Presupuesto: `asyncio.wait_for(..., 60)` para que un desafío lento no retrase al resto.
- Precalentado opcional al arrancar el bot (resolver ambos desafíos en segundo plano) para
  que el primer `!buscar` no pague los ~15 s.

#### Fase 5 — Bot y UX (2 h)

- `ICONO_SITIO` y `COLOR_SITIO` en `bot.py`.
- Log de progreso: «resolviendo verificación de <host> (~15 s)» para que no parezca colgado.
- `autor()`: chochox no tiene taxonomía de artista (va en el slug/título); devolver `None` o
  el nombre entre guiones. vercomicsporno tiene índice `/artistas/`: enlazarlo si es barato.

#### Fase 6 — Pruebas y documentación (medio día)

- `tests/test_chochox.py` y `tests/test_vercomicsporno.py` con dos fixtures HTML pequeños
  (título + 3 imágenes + banner): comprobar título, orden, filtrado y que no se cuela el logo.
- `tests/test_navegador.py`: solo la detección de desafío por título (sin abrir Chromium).
- Script `scripts/smoke_cf.py` (no test): imprime estado, páginas encontradas y tiempos.
- README: filas de la tabla, sección de Cloudflare y nota de que estos dos sitios necesitan
  Chromium.
- `HOW_TO_ADD_SITES.md`: excepción documentada a la regla «no esquives protecciones» — si no
  se documenta, el próximo colaborador la revertirá otra vez.

#### Fase 7 — Docker/NAS (2 h)

- `Dockerfile.bot`: confirmar que la base `base-20260816` trae Chromium; si no,
  `RUN playwright install chromium` (idempotente) y `playwright install-deps`.
- Volumen para `CF_PERFIL`, si se quiere que el `cf_clearance` sobreviva a reconstrucciones.
- Probar en el NAS: memoria en reposo, tiempo del primer `!buscar` y de una descarga.

---

## 4. Evidencia de los sitios (recon, 26/11/2026)

Sacada de instantáneas de Wayback Machine (los sitios en vivo están tras el desafío).

**chochox.com** — WordPress, tema propio, un post por cómic:

```html
<h1>Juegos de Gemelos – Crock2121</h1>
<div class="posts">
  <img width="1080" height="1196" src="https://chochox.com/wp-content/uploads/2024/12/Juegos-de-Gemelos-Crock-01.webp" alt="Juegos De Gemelos Crock 01" class="wp-image-135638"/>
  ...
</div>
```

- Búsqueda: `/?s=<texto>` (formulario `name="s"`, acción `https://chochox.com`).
- Categorías: `/category/<slug>/` (p. ej. `/category/comics-porno-sex/`).
- El artista no es taxonomía: va en el slug y en el título (`...-crock2121`).
- Las imágenes son WebP y se sirven directas con `src`.

**vercomicsporno.com** — WordPress, tema `vercomics`, un post por cómic:

```html
<h1 class="titl">[Harkart] Steven’s Desire 2 (Steven Universe)</h1>
<div class="posts">
  <img src="https://vercomicsporno.com/wp-content/uploads/2024/10/0cgeqwqi-SD01.jpg" alt="[Harkart] Steven’s Desire 2 (Steven Universe)">
  ...
</div>
```

- Búsqueda: `/comics-porno/?s=<texto>` (mismo formulario `name="s"`).
- Índice de artistas: `/artistas`.
- Sin paginación dentro del cómic en la muestra: todas las páginas van en un solo post.

---

## 5. Riesgos

| Riesgo | Mitigación |
|---|---|
| Cloudflare detecta el headless y no suelta el desafío | Fase 0 antes de nada; perfil persistente y flags anti-detección; si falla, B no sigue |
| `cf_clearance` caduca o cambia de huella | Reintento único con navegación nueva; caché de 10 min por host |
| Chromium se come la RAM del NAS | Un solo navegador, cierre a los 10 min, `--disable-dev-shm-usage` |
| El bot parece colgado en el primer `!buscar` | Precalentado al arrancar + log explícito |
| Cambios de tema/DOM | Selectores aislados por handler + fixtures en tests |
| Otro colaborador revierte el cambio | Decisión de política por escrito en README y `HOW_TO_ADD_SITES.md` |
| Condiciones de Cloudflare | Decisión consciente del dueño del repo; no se puede «mitigar» |

---

## 6. Criterios de aceptación (B)

1. `!buscar <algo>` devuelve resultados de los dos sitios con portada y bandera.
2. Pegar una URL de cómic descarga un PDF con todas las páginas **en orden**.
3. Un enlace de un sitio que no es cómic (categoría, artista, home) o bien se rechaza con
   mensaje claro, o bien encola la lista (decisión de alcance).
4. Segundo uso en la misma sesión: sin desafío y en menos de 2 s por página.
5. Reinicio del bot: el perfil guardado evita el desafío si `cf_clearance` sigue vivo.
6. El resto de sitios no cambia de comportamiento ni de tiempos.
7. Tests unitarios en verde sin red.

---

## 7. Plan A (alternativa que sí cumple la política)

Sin navegador y sin tocar protecciones:

1. En `core/handler.py`, cuando la URL sea de `BLOQUEADOS_CLOUDFLARE`, extraer el título del
   slug (`.../harkart-stevens-desire-2-steven-universe/` → `harkart stevens desire 2 steven
   universe`), limpiar el sufijo del artista y lanzar `busqueda.buscar()`.
2. Responder con los resultados ya listos para descargar (mismo embed que `!buscar`) y un
   aviso de una línea: «chochox.com bloquea la descarga automática; esto es lo mismo en los
   sitios compatibles».
3. Opcional: botón «Ver todos» reutilizando `ResultadosPaginadosView`.

Coste: 2–3 h. Sin Chromium, sin perfil, sin riesgo de que lo reviertan, y resuelve el caso
habitual (la obra está en otro sitio). Lo que no resuelve: exclusivos de esos dos dominios.

---

## 8. Estimación

| Vía | Trabajo | Riesgo de reversión |
|---|---|---|
| A | 2–3 h | ninguno |
| B | 1,5–2 días | alto (política del PR #14) |
| C | 1 día + contenedor | alto |

**Siguiente paso:** elegir A o B. Si es B, ejecutar primero la Fase 0 y decidir con el
resultado en la mano.
