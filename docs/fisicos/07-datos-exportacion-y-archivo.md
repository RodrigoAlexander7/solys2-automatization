# Datos, exportación y archivo

## Dónde se guardan

| Almacén | Contenido | Cuánto tiempo |
|---|---|---|
| `data/acquisition.sqlite3` | Muestras de 1 s, promedios de 1 min, eventos | 1 s: 30 días; minutos y eventos: siempre |
| `data/archive/AAAA/MM/AAAA-MM-DD.parquet` | Muestras de 1 s de cada día UTC cerrado | Siempre |
| `data/observatory.sqlite3` | Configuraciones y bitácora | Siempre |

Un día se archiva en Parquet una hora después de terminar. Solo se retira de la
base activa cuando ya pasó la retención (30 días por defecto) y el archivo se
verificó por número de filas y SHA-256. Las consultas y descargas leen las dos
fuentes sin duplicar filas.

## Columnas del CSV de 1 s

| Columna | Significado |
|---|---|
| `time_utc`, `time_local` | Hora de la lectura (PC), UTC y local |
| `run_id`, `cycle` | Corrida y ciclo (el ciclo agrupa lecturas secuenciales) |
| `address`, `label`, `sensor_type`, `role` | Instrumento, etiqueta, tipo y rol vigente |
| `measurement_wm2` | Magnitud principal: global o difusa (piranómetro) o Ld↓ (pirgeómetro) |
| `irradiance_wm2`, `raw_irradiance_wm2` | Registro 5 y 6 ya convertidos (en el SGR4, radiación neta) |
| `lw_down_wm2`, `raw_lw_down_wm2` | Registros 10 y 11 del SGR4 |
| `lw_down_calc_wm2` | Neta + σT⁴ (derivada, comprobación) |
| `stdev_wm2`, `body_temp_c`, `supply_v`, `rh_pct`, `pressure_hpa` | Diagnósticos del sensor |
| `mode`, `status_hex`, `status_flags`, `valid`, `rtt_ms`, `error` | Estado y calidad de la lectura |
| `solar_zenith_deg`, `kt`, `dhi_ghi_ratio`, `dni_est_wm2` | Derivados |
| `qc_ppl`, `qc_erl`, `qc_ratio`, `qc_shading`, `qc_comparison` | Pruebas: `ok`, `fail`, `na` |
| `qc_summary`, `qc_notes` | Resumen (`ok`, `suspect`, `bad`, `na`) y motivos |
| `config_version` | Versión de configuración usada para interpretar la fila |

El CSV de 1 minuto trae `n_samples`, `n_valid`, `complete` (≥ 80 % de las
esperadas), media, mínimo, máximo, desviación y las mismas columnas derivadas.

Un valor vacío es **ausente**, no cero.

## Leer el archivo Parquet

Cada Parquet incluye las palabras Modbus originales (`reg_2` … `reg_17`) y en
sus metadatos la versión de esquema y de conversión. Excel no lo abre; use la
descarga CSV o:

```python
import pandas as pd
df = pd.read_parquet("data/archive/2026/06/2026-06-21.parquet")
```

```r
df <- arrow::read_parquet("data/archive/2026/06/2026-06-21.parquet")
```

```matlab
T = parquetread("data/archive/2026/06/2026-06-21.parquet");
```

## Figuras

La figura PNG del histórico se genera al pedirla y no se guarda. Usa muestras
de 1 s hasta 6 h y promedios de minuto en intervalos mayores, e incluye un pie
con el intervalo, los instrumentos, la versión del programa y la fuente de las
pruebas de calidad. El botón **PNG** de cada gráfico descarga la vista actual.
