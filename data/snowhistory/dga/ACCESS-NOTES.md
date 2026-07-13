# DGA Chile snow data — access notes (PowderBench recon)

Date of recon: 2026-07-12. UA: PowderBench-recon/0.1 (+https://powderbench.com). ~7 paced requests this run.

## Outcome
- **Station inventory: CAPTURED** (105 snow stations) → `dga-catalog.json`.
- **Historical time series: NOT harvested.** No anonymous machine-readable path exists; the only
  route is the reCAPTCHA-gated JSF report form. See below.

## What was probed
1. **BNAConsultas JSF form** `https://snia.mop.gob.cl/BNAConsultas/reportes`
   - Single `<form action="/BNAConsultas/reportes" method="post">`, RichFaces/JSF (`javax.faces.ViewState`).
   - XLS export is `RichFaces.ajax("j_idt27", ...)` — a stateful AJAX POST needing the live ViewState.
   - **reCAPTCHA v2** (sitekey `6LcRYtwqAAAAACZzq5MG06b68xaZpAhVHOJaLCFt`); onload `desactivarBoton`
     keeps the export button disabled until the captcha is solved. Curl/HTTP automation cannot pass it.
   - Prior run drove it with a real Chrome (CDP) and was stopped at the reCAPTCHA (`page8_recaptcha_attempt.png`).
2. **SNIA portal-web SPA** `https://snia.mop.gob.cl/portal-web/` (Angular).
   - REST bases in `main.*.js`: `dga-rest-portal`, `dga-rest-common`, `auth-rest-portal`.
   - This portal is water RIGHTS / expedientes / certificados only — **no hydromet time series**.
   - Endpoints require a Clave Única bearer token: `GET /dga-rest-common/geo/v1/regiones` → `401 Token no valido`.
3. **DMC / Meteochile** `https://climatologia.meteochile.gob.cl/` — national weather service; publishes
   DMC's own stations, not DGA's ruta-de-nieve (snow-course) SWE network. Not a substitute.
4. **datos.gob.cl CKAN** — DGA org only publishes water-rights (derechos) datasets (CC-BY). No snow series.

## Source of the catalog
DGA "Informe Nacional" station inventory XLSX (`dga_informe_nacional.xlsx`, generated 2026-01-09 by
DGA), one sheet "Nacional", 3810 active stations. Columns: Código, Estación, Fecha Instalación, Región,
Provincia, Comuna, Cuenca/SubCuenca/SubSubCuenca, UTM WGS84 N/E, Datum, Latitud, Longitud, Altitud,
Tipo de Estación. Snow types present: **RUTA DE NIEVE ×31**, **NIEVES Y GLACIARES ×75** (74 unique).
Lat/lon converted from DMS to decimal; Chile is S/W so both negated.

## Variable / unit definitions (per DGA convention; confirm in the generated report header)
- Ruta de nieve (snow course): **altura de nieve** = snow depth (cm); **equivalente en agua de nieve
  (EAN / SWE)** = water equivalent, reported in mm (sometimes cm) of water. Verify per report.
- CSV schema to use when harvested: SWE → `date,swe_mm`; depth → `date,depth_cm`; monthly → date = 1st of month.

## "Datos provisorios" caveat
Not present in the captured HTML (it appears in the generated report/xlsx, which we could not obtain).
DGA reports normally carry a "datos provisorios sujetos a validación" note — capture the exact wording
from a real generated report in a follow-up run.

## Manual / browser steps for a follow-up run (to actually get series)
1. Open `https://snia.mop.gob.cl/BNAConsultas/reportes` in a real browser (CDP session already scaffolded:
   `cdp_driver.py`, `download_csv.py`, `download_csv2.py`, `chrome-profile/` in the parent scratchpad).
2. Pick report type **"Reportes Meteorológicos"** (nieve variables live under meteorológica/nivométrica),
   select Región → Cuenca → Estación (use codes in `dga-catalog.json`), set the date range, choose XLS.
3. **Solve the reCAPTCHA manually** (this is the only blocker), then click the (now-enabled) XLS button.
4. Capture the XLS via CDP `Network.getResponseBody` / download behavior (scripts already written).
5. Repeat per flagship station; parse XLS → CSV with the schema above.
Alternative: file a data request with DGA (dga@mop.gov.cl) / the SNIA "consulta pública" channel for a
bulk snow-course export.
