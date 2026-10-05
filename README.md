# Feed con marco AO — La Espumería (BullMetrix)

Reemplaza el origen de datos **"New Feed Orka"** del catálogo **VtexIntegration-laespumeria** (Meta).
Genera para cada SKU la imagen 1080×1080 con el marco AO (% OFF, precio tachado, precio final, cuotas)
y publica un CSV con los **mismos IDs y columnas** que el feed de Orka, así los conjuntos de productos y anuncios no se rompen.

## Cómo funciona
1. Lee el XML de la colección **CATALOGO ORKA** de VTEX (`XMLData/feed_prueba.xml`, se regenera todos los días). El cliente elige qué productos entran sumándolos o sacándolos de esa colección. (Alternativa: `SOURCE=api` lee todo el catálogo por la API pública.)
2. Por SKU: precio de lista (`ListPrice`), precio (`Price`), máximo de cuotas sin interés con tarjeta de crédito y la **primera foto** (igual que Orka).
3. Renderiza solo lo que cambió (hash de foto + precios + cuotas). Las imágenes van a `gs://<bucket>/la-espumeria/img/<sku>_<hash>.jpg`; el nombre cambia cuando cambia el precio, así Meta vuelve a bajar la imagen.
4. Publica `https://storage.googleapis.com/<bucket>/la-espumeria/feed.csv`.
5. Si el feed queda vacío (VTEX caído, etc.) **no publica**, para no vaciar el catálogo.

Reglas del diseño:
- `% OFF = 1 − Price / ListPrice`, redondeado. Si es menor a 5%, se usa la variante del marco **sin globo** y sin precio tachado.
- Cuotas: `"12 Cuotas sin interés de $X"` con el valor que devuelve VTEX (redondeado).
- Si el texto no entra en la píldora, se achica automáticamente.

## Archivos
- `render.py` — dibuja la imagen (marco + fuente libre Nunito).
- `vtex.py` — lectura del catálogo VTEX.
- `main.py` — orquesta, sube a GCS y publica el CSV.
- `custom_labels.csv` — `custom_label_1 = "mas vistos"` copiado del feed de Orka (94 SKUs). Se puede reemplazar por un Sheet publicado como CSV con `OVERRIDES_CSV_URL`.
- `deploy.sh` — crea bucket, service account, Cloud Run Job y Cloud Scheduler (cada hora, minuto 20).

## Deploy (GitHub, gratis)
- GitHub Actions corre `main.py` cada hora y publica `site/` en GitHub Pages.
- Settings → Pages → Source: **GitHub Actions**.
- Feed: `https://<usuario>.github.io/feed-la-espumeria/la-espumeria/feed.csv`
- Correr a mano: Actions → "Feed La Espumería (marco AO)" → Run workflow.

## Cambio en Meta (Commerce Manager)
1. Catálogo **VtexIntegration-laespumeria** → Orígenes de datos → **New Feed Orka** → Configuración.
2. Reemplazar la URL del Google Sheet de Orka por la de `feed.csv` (mantener “Se reemplaza cada hora”).
   Editar la URL del mismo origen (en vez de crear uno nuevo) conserva el historial de los productos.
3. Forzar “Actualizar” y revisar en Productos que las imágenes nuevas aparezcan sin errores.
4. Recién ahí pedir/aceptar que Orka dé de baja su automatización.

## Mantenimiento
- Cambio de diseño: reemplazar `frame_ao.png` (1080×1080, ventana transparente en x 140–940, y 215–863), ajustar posiciones en `render.py` y subir `TEMPLATE_VERSION`.
- Logs: Cloud Run → Jobs → `feed-la-espumeria` → Ejecuciones.
