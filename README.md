# Adquisición SMP22x por RS-485 / Modbus RTU

Lectura de tres piranómetros **Kipp & Zonen SMP22x** (n.º de producto 0374960),
direcciones Modbus **2, 3 y 4**, conectados en paralelo a un **PMU485 Smart
Setup Hub** (0382460) enchufado por USB al PC. El software guarda los datos en
CSV y genera las gráficas de la prueba.

Documentación oficial en [`doc/`](doc/): hoja de datos SMPx 2025 y manual de
operación SMP10x/SMP22x V20. Las referencias `§` de este README y del código son
secciones de ese manual.

## 1. Conexión

El PMU485 es a la vez un conversor USB ↔ RS-485 de 2 hilos (chip FTDI, Windows
lo reconoce como `USB Serial Port (COMx)`) y una fuente de 12 V alimentada por
el USB. Puede alimentar y comunicar hasta 10 radiómetros en paralelo:
50 mA para el primero + 10 mA por cada sensor adicional [1][2].

| Hilo del SMP | Función (§7.3.5)          | Borne del PMU485       |
|--------------|---------------------------|------------------------|
| Blanco       | Alimentación 5–30 V DC    | +12 V                  |
| Negro        | Tierra de alimentación    | GND                    |
| Amarillo     | RS-485 Data + [3]         | B / +                  |
| Gris         | RS-485 Data − [3]         | A / −                  |
| Azul         | Común Modbus              | GND / común            |
| Rojo, verde, marrón | No conectados      | Aislar                 |

- El manual V20 no dice cuál de los dos hilos (amarillo o gris) es + y cuál es −. La hoja de
  Kintech para el SMP10 indica amarillo = Data+ y gris = Data− [3]. Si ningún
  sensor responde, intercambie amarillo y gris; hacerlo no daña nada.
- Los tres sensores deben tener **la misma velocidad y paridad** y
  **direcciones distintas** (§11.1). Fábrica: 19200 baud, 8 bits, paridad par,
  1 bit de parada (§8.1).
- Con cables largos, ponga una resistencia de 120–150 Ω entre A y B en el
  último sensor del bus (§7.3.5). Con cables de 10 m a 19200 baud normalmente
  no hace falta.
- **No abra SmartExplorer al mismo tiempo que este programa.** El puerto COM
  solo lo puede usar un programa a la vez, y un bus Modbus admite un único maestro.

## 2. Instalación

```bash
python -m venv .venv
```
```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

## 3. Uso

```bash
.venv\Scripts\python -m smp22x ports
```
Muestra los puertos COM. El PMU485 aparece marcado como FTDI. Con `--port auto`,
que es el valor por defecto, el programa lo elige solo.

```bash
.venv\Scripts\python -m smp22x scan
```
Busca sensores en las direcciones 1–20 a 19200 8E1. Con `--all-baud` prueba
además otras velocidades y paridades.

```bash
.venv\Scripts\python -m smp22x info --clear
```
Muestra, para cada sensor, el modelo, el número de serie, el modo, los flags de
estado, la irradiancia, la temperatura, la tensión de alimentación, la humedad y
la presión internas y el tiempo de respuesta. `--clear` borra los flags que el
sensor activa siempre al arrancar (§15.8).

```bash
.venv\Scripts\python -m smp22x log -d 30m --label prueba1
```
Adquiere datos durante 30 minutos (`-d 90s`, `-d 2h`… o sin `-d` hasta pulsar
Ctrl+C) y al terminar genera las gráficas.

```bash
.venv\Scripts\python -m smp22x plot data\2026-09-28_120000_prueba1
```
Vuelve a generar las gráficas de una prueba ya guardada.

Añadiendo `--simulate` a `scan`, `info` o `log` se usan tres sensores simulados
en las direcciones 2, 3 y 4, sin hardware. Sirve para probar el programa. Las
carpetas simuladas terminan en `_SIM` y las gráficas lo indican.

Tests: `.venv\Scripts\python -m unittest discover -s tests`

## 4. ¿Cada cuánto leer?

**Recomendación: leer cada 1 s y guardar promedios de 1 minuto.** Es el valor
por defecto (`--interval 1`).

| Criterio | Dato | Fuente |
|---|---|---|
| Actualización del registro de irradiancia | 1 vez por segundo | Kipp & Zonen, *Pyranometer interface options* [4] |
| Tiempo de respuesta del sensor | 63 % < 0,7 s · 95 % < 2 s | Hoja de datos SMPx 2025 |
| Recomendación del fabricante | "muestrear cada segundo y guardar promedios de un minuto" | Manual V20 §9.2 |
| Valor por defecto del software del fabricante | 1 lectura por segundo | Manual SMP (anterior), §4.1 [5] |
| IEC 61724-1, Clase A | muestreo ≤ 3 s, registro ≤ 1 min | [6] |

- **Menos de 1 s no aporta información.** El registro solo cambia una vez por
  segundo, así que se leerían valores repetidos y se ocuparía el bus para nada.
  Se puede comprobar con `log -i 0.5`: en `resumen.csv`, la columna
  `repetidos_%` subirá a ~50 %.
- **Más de 3 s** deja de cumplir la Clase A de IEC 61724-1. El programa avisa.
- `IO_STDEV_SENSOR1` es la desviación desde la lectura anterior (§15.6). A 1 s
  sale casi 0. Para ver la variabilidad dentro de cada minuto, use la columna
  `std_wm2` de `minute_avg.csv`.
- Humedad relativa y presión internas (diagnóstico del desecante): se leen cada
  60 s (`--diag-every`).

### Carga del bus a 19200 baud 8E1 (11 bits por carácter = 0,573 ms)

Cada ciclo hace una lectura FC04 de 8 registros (2–9) por sensor:

| Parte | Bytes | Tiempo |
|---|---|---|
| Petición | 8 | 4,6 ms |
| Respuesta (8 registros) | 21 | 12,0 ms |
| 2 silencios t3.5 | – | 4,0 ms |
| **Total en el cable** | | **20,6 ms** |
| + latencia USB del FTDI (16 ms por defecto) + tiempo de respuesta del sensor | | ≈ 10–30 ms |

Con tres sensores, cada ciclo ocupa unos 60–150 ms. Leyendo cada segundo, el bus
queda ocupado un 6–15 %, así que hay margen de sobra: aunque un sensor no
responda y se espere su timeout de 0,3 s, el ciclo sigue cabiendo en 1 s. El
tiempo real de cada lectura se guarda en `rtt_ms`, y el resumen final indica la
ocupación medida.

Por qué el ciclo es robusto:

- Las peticiones van **en serie**, una detrás de otra. Modbus RTU es semidúplex
  y con un solo maestro, así que nunca se hacen peticiones en paralelo.
- Los ciclos arrancan en segundos exactos del reloj, de modo que cada minuto
  tiene exactamente 60 muestras.
- Si un sensor falla 5 veces seguidas, solo se le vuelve a consultar cada 10
  ciclos. Así no se retrasa a los demás.
- Si se desconecta el USB, el programa intenta reabrir el puerto cada 2 s.

### Tiempos de estabilización

- Tras encender, el sensor necesita **hasta 3 h** para alcanzar una temperatura
  estable. En ese tiempo las medidas pueden desviarse (§7.3.5).
- Tras cambiar la dirección o la velocidad, el sensor se reinicia y tarda
  **~1 min** en estar listo (§8.1.3).

## 5. Archivos de salida

Por cada prueba se crea `data/<fecha>_<hora>[_etiqueta]/` con:

- **`raw_1s.csv`**: una fila por sensor y ciclo. Columnas:
  - `time_utc` y `time_local`: marca de tiempo.
  - `cycle`: número de ciclo. Las filas del mismo ciclo corresponden al mismo instante.
  - `address`: dirección del sensor.
  - `irradiance_wm2`: `IO_SENSOR1_DATA`, irradiancia compensada en temperatura.
  - `raw_irradiance_wm2`: `IO_RAW_SENSOR1_DATA`.
  - `stdev_wm2`, `body_temp_c`, `supply_v`.
  - `rh_pct` y `pressure_hpa`: solo cada 60 s.
  - `mode`, `status_hex`, `status_flags`.
  - `valid`: 1 si el dato es utilizable.
  - `rtt_ms`: tiempo de respuesta de la lectura.
  - `error`: descripción del fallo, si lo hubo.
- **`minute_avg.csv`**: por minuto y sensor, número de muestras, número de
  válidas, media, mínimo, máximo y desviación típica. `complete=1` si hay al
  menos un 80 % de muestras válidas.
- **`run.json`**: configuración, identificación de cada sensor (modelo, número
  de serie, firmware) y estadísticas del bus.
- **`irradiancia.png`**: tres paneles con la irradiancia a 1 s (puntos = media de
  1 min), la diferencia de cada sensor respecto a la media de los tres y la
  temperatura interna.
- **`comunicacion.png`**: tiempo de respuesta por lectura, timeouts y tensión de
  alimentación.
- **`resumen.csv`**: estadísticas por sensor. También se imprime en consola.

### Cuándo una muestra cuenta como válida (`valid = 1`)

Una muestra es válida si el sensor respondió, está en modo normal (1) y no tiene
activo ninguno de estos flags: `VOID_DATA`, `OVERFLOW`, `UNDERFLOW`,
`ADC_ERROR`, `CALIBRATION_ERROR` ni `BODY_TEMP_ERROR` (§15.6).

- `ERROR` y `POWER_FAILED` se activan **siempre** al arrancar el sensor (§15.8).
  Por sí solos no invalidan el dato. El programa los borra escribiendo el coil
  `IO_CLEAR_ERROR`, como hace el ejemplo del manual (§15.11). Ese coil no cambia
  la configuración del sensor.
- `DESICCANT_WARNING` indica que hay que cambiar el desecante. Queda registrado,
  pero no invalida el dato.

## 6. Limitaciones y puntos a verificar con el hardware

- Por ahora el software se ha probado con el simulador y con tests unitarios,
  **no contra los sensores reales**. La primera prueba con hardware debería ser
  `ports` → `scan` → `info`.
- Unidad de `IO_PRESSURE`: el manual dice "hPa" en §15.2 y "0,1 hPa" en §15.6.
  El programa decide según la magnitud del valor (ver `sensor.pressure_to_hpa`).
  Compruebe que `info` muestra un valor razonable, alrededor de 1000 hPa.
- El PMU485 está pensado como herramienta de instalación y servicio, no para
  medir de forma permanente [2]. Además, el manual pide que el conversor RS-485
  tenga aislamiento galvánico, porque las fuentes de los portátiles generan
  picos (§7.3.5). Para una instalación fija, use una fuente de 12 V y un
  conversor aislado, o el Smart Powered Hub (0382440).
- El programa de ejemplo en C del manual (§15.11) omite los `break` del
  `switch`. Tal como está escrito, siempre daría 0.0; no lo copie literalmente.

## Fuentes

1. Kipp & Zonen, *New accessory: PMU485 Smart Setup Hub* y *Instruction Manual PMU485* — <https://www.kippzonen.com/Download/1061/Manual-PMU485-Smart-Setup-Hub>
2. Equinox Instruments, ficha PMU485 (12 V DC, 50 mA + 10 mA/sensor, hasta 10 radiómetros, herramienta de instalación/servicio) — <https://www.equinoxinstruments.co.uk/products/accessories/pmu485>
3. Kintech Engineering, *Kipp & Zonen SMP10, output RS485* (asignación de hilos) — <https://www.kintech-engineering.com/pdf_docs/EN_Pyranometer_Kipp_AND_Zonen_RS485_output.pdf>
4. Kipp & Zonen, *Pyranometer interface options* V1.0 (2021): "register, which is updated once per second" — <https://www.kippzonen.com/Download/1084/Interface-options-for-Kipp-Zonen-Pyranometers>
5. Kipp & Zonen, *Instruction Manual SMP series* (edición anterior, vía Campbell Scientific) — <https://s.campbellsci.com/documents/ca/manuals/smp-series_man.pdf>
6. IEC 61724-1:2021, tabla de intervalos de muestreo y registro (resumen) — <https://www.linkedin.com/pulse/part-1-2-weather-data-acquisition-according-iec-61724-1-thomas-boldt>
