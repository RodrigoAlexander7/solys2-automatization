# De Modbus a una medida física

Describe la implementación actual con SQLite y el dashboard FastAPI/SvelteKit.
Para ejecutarlo consulte [operación](operacion-dashboard.md); para conocer qué
se calcula después de convertir, [tratamiento de datos](tratamiento-datos.md).

## 1. Dirección del equipo y dirección del registro

La **dirección del equipo** elige el sensor; la **dirección del registro** elige
un dato dentro de ese sensor. No son intercambiables.

| Equipo en este montaje | Instrumento | Magnitud principal prevista | Bloque solicitado |
|---|---|---|---|
| 2 | Pirgeómetro SGR4 | Onda larga descendente Ld↓ | Registros 2–11, 10 palabras |
| 3 | Piranómetro SMP22x | Irradiancia de onda corta | Registros 2–9, 8 palabras |
| 4 | Piranómetro SMP22x | Irradiancia de onda corta | Registros 2–9, 8 palabras |

Las direcciones 2, 3 y 4 son configuración del montaje, no números de modelo
prescritos por el fabricante. `--addresses` selecciona equipos y `--pyrgeometers`
indica cuáles interpretar como pirgeómetros (por defecto, 2). La dirección no
informa de orientación ni sombreado; llamar «global» a la irradiancia presupone
una instalación apropiada del piranómetro.

Ejemplo: `read_input_registers(slave=2, start=2, count=10)` usa FC04 para pedir al
equipo 2 sus registros 2 a 11. Devuelve diez enteros: el primero corresponde al
registro 2. `IO_SENSOR2_DATA` es el segundo canal de un equipo, **no** el equipo 2.

Aquí usamos **direcciones PDU base cero**, como el manual y minimalmodbus.
Algunas herramientas muestran números base uno: PDU 5 aparece como registro 6
(SMP §15.5, p. 47). El input register 10 y el coil 10 pertenecen a espacios
distintos: FC04 lee Ld↓ y FC05 escribe la orden de borrar errores.

## 2. Documentos y páginas verificadas

En ambos manuales, la página impresa coincide con la página del PDF contando
desde 1. Los enlaces incluyen `#page=N`, que algunos visores pueden ignorar.
`§` significa sección.

| Clave | Documento local | Qué respalda |
|---|---|---|
| SMP | [Manual SMP10x/SMP22x V20](../references/pyranometer-SMP22x/Operational_Manual_SMP10x_SMP22x_Smart_Pyranometers_V20.pdf) | §15.2, pp. 43–45: direcciones y tipos; §15.6, pp. 47–49: significado, bits y conversiones |
| SGR | [Manual CGR4/SGR4 V10](../references/pyrgeometer-SGR4/Operational_Manual_CGR4_SGR4_V10.pdf) | §7.1.5 p. 17: fórmula; §8.1 p. 24: comunicación; §13.1 p. 29: prestaciones |

**SGR no incluye tabla de registros Modbus.** Las filas «only for SGR» están
en SMP; no certifican compatibilidad con cualquier firmware SGR4. La prueba 01
tiene modelos de datos 100 y 102; SMP p. 47 exige revisar diferencias para
versiones anteriores a 108. Los códigos 607 y 611 se asocian a modelos por lo
observado en el montaje y la identificación aportada por el usuario, no por una
tabla oficial de esos códigos.

## 3. Enteros y escala

Modbus transporta palabras de 16 bits. La librería entrega enteros de 0 a 65535;
el manual indica cómo interpretarlos:

- **U16:** sin signo, 0 a 65535; códigos y máscaras.
- **S16:** con signo, −32768 a 32767, complemento a dos. `to_s16` resta 65536
  si está activo el bit 15. Así, 65535 representa −1.
- Interpretar el signo y aplicar la escala son dos pasos distintos.

El protocolo también puede transportar datos que ocupan varias palabras. SMP
pp. 44–45 describe S32 y F32 que este programa no utiliza.

## 4. Tabla de lectura periódica

`S(x)` significa interpretar con signo; `D(f)` es el divisor elegido por el
registro 4. La columna «campo» corresponde a `Sample` en
[sensor.py](../smp22x/sensor.py), no siempre a una columna persistida.

| PDU | Nombre oficial | Qué representa | Tipo y conversión actual | Unidad / campo | Fuente exacta |
|---|---|---|---|---|---|
| 2 | IO_OPERATIONAL_MODE | 1 normal, 2 servicio, 3 calibración, 4 fábrica, 5 error | U16, sin escala | Código / `mode` | SMP §15.2 p. 43; §15.6 p. 47 |
| 3 | IO_STATUS_FLAGS | Estado; un significado por bit | U16, máscara | Máscara / `status` | SMP §15.2 p. 43; §15.6 pp. 47–48 |
| 4 | IO_SCALE_FACTOR | Escala de 5, 6, 10 y 11 | S16: `S(x)` | Factor / `scale_factor` | SMP §15.2 p. 43; §15.6 p. 48 |
| 5 | IO_SENSOR1_DATA | SMP: irradiancia compensada. SGR: radiación neta de onda larga del detector | S16: `S(x)/D(f)` | W/m² / `irradiance` | SMP §15.2 p. 43 (incluye neta SGR); §15.6 p. 48 |
| 6 | IO_RAW_SENSOR1_DATA | Canal 1 calibrado, sin linealizar ni compensar en temperatura | S16: `S(x)/D(f)` | W/m² / `raw_irradiance` | SMP §15.2 p. 43; §15.6 p. 48 |
| 7 | IO_STDEV_SENSOR1 | Desviación del canal 1 desde la lectura anterior | S16: `S(x)/10` | W/m² / `stdev` | SMP §15.2 p. 43; §15.6 pp. 48–49 |
| 8 | IO_BODY_TEMPERATURE | Temperatura del cuerpo | S16: `S(x)/10` | °C / `body_temp_c` | SMP §15.2 p. 43; §15.6 p. 49 |
| 9 | IO_EXT_POWER_SENSOR | Alimentación en el conector del equipo | S16: `S(x)/10` | V / `supply_v` | SMP §15.2 p. 43; §15.6 p. 49 |
| 10 | IO_SENSOR2_DATA | Solo SGR: onda larga descendente compensada Ld↓ | S16: `S(x)/D(f)` | W/m² / `lw_down` | SMP §15.2 p. 43; escala §15.6 p. 48 |
| 11 | IO_RAW_SENSOR2_DATA | Solo SGR: onda larga descendente del canal bruto | S16: `S(x)/D(f)` | W/m² / `raw_lw_down` | SMP §15.2 p. 44; escala §15.6 p. 48 |

«Raw» aquí no significa cuentas del ADC ni el entero recibido: el programa ya
lo convierte a W/m². El manual no desarrolla todas las correcciones del canal
2 bruto; no deben suponerse por analogía. El registro 7 corresponde al canal 1:
**en el SGR4 no es la desviación de Ld↓**. Leerlo reinicia su cálculo (pp. 48–49).

### Factor del registro 4

| Factor con signo | Divisor `D(f)` | Operación | Ejemplo |
|---|---|---|---|
| 2 | 100 | ÷100 | 8123 → 81,23 W/m² |
| 1 | 10 | ÷10 | 8123 → 812,3 W/m² |
| 0 | 1 | Conservar valor | 812 → 812 W/m² |
| −1 | 0,1 | ×10 | 81 → 810 W/m² |

Fuente: [SMP §15.6 p. 48](../references/pyranometer-SMP22x/Operational_Manual_SMP10x_SMP22x_Smart_Pyranometers_V20.pdf#page=48).
No se divide directamente entre el factor. Otro factor produce `None` y error.
Los registros 7, 8 y 9 tienen escala fija, independiente del registro 4.

Ejemplo negativo: 63936 se interpreta como −1600; con factor 1 da −160 W/m².
Puede ser una radiación neta plausible para un SGR4. Una temperatura bruta 253
da 25,3 °C con cualquier factor. Resolución numérica no equivale a exactitud.

## 5. Identificación y diagnóstico

| PDU | Nombre | Significado y conversión | Uso actual | Fuente SMP |
|---|---|---|---|---|
| 0 | IO_DEVICE_TYPE | U16, modelo; 635 y 636 documentados | Identificación inicial | §15.2 p. 43; §15.6 p. 47 |
| 1 | IO_DATAMODEL_VERSION | U16, versión del mapa, distinta de software | Aviso de compatibilidad | §15.2 p. 43; §15.6 p. 47 |
| 16 | IO_RH | U16 ÷10 → % de humedad interna | Diagnóstico; pendiente para firmware antiguo | §15.2 p. 44; §15.6 p. 49 |
| 17 | IO_PRESSURE | U16; bruto >2000 → ÷10, si no se conserva → hPa | Heurística del proyecto | §15.2 p. 44 frente a §15.6 p. 49 |
| 26 | IO_ERROR_CODE | U16, último error | Constante definida, no consultada actualmente | §15.2 p. 44 |
| 41 | IO_BATCH_NUMBER | U16, año de lote YY | Identificación inicial | §15.2 p. 44 |
| 42 | IO_SERIAL_NUMBER | U16, número dentro del lote | Identificación inicial | §15.2 p. 44 |
| 43 | IO_SOFTWARE_VERSION | U16, versión del software | Identificación inicial | §15.2 p. 44 |
| 44 | IO_HARDWARE_VERSION | U16, versión del hardware | Identificación inicial | §15.2 p. 44 |
| 45 | IO_NODE_ID | U16, dirección del equipo | Comparación con dirección consultada | §15.2 p. 45 |

En p. 49 la humedad aparece con «hPa», pero p. 44 indica 0,1 %. Es una
inconsistencia del documento. Para presión, p. 44 dice hPa y p. 49 dice 0,1 hPa.
El umbral 2000 es una heurística; un resultado razonable **no valida** el mapa.
Los registros 16–17 podrían tener otra función en firmware antiguo (ver
[D12](decisiones-tecnicas.md#d12-compatibilidad-con-el-firmware-detectado-en-la-prueba-01)).
El código sigue leyéndolos cada 60 s por defecto, pero no deben usarse para
alertas ni conclusiones científicas sin confirmar su significado.

## 6. Bits, validez y orden de control

| Bit del registro 3 | Nombre en el código | Tratamiento actual |
|---|---|---|
| 0 | VOID_DATA | Invalida |
| 1 | OVERFLOW | Invalida |
| 2 | UNDERFLOW | Invalida |
| 3 | ERROR | No invalida por sí solo; se intenta borrar |
| 4 | ADC_ERROR | Invalida; se intenta borrar |
| 5 | DAC_ERROR | No invalida por sí solo; se intenta borrar |
| 6 | CALIBRATION_ERROR | Invalida |
| 7 | UPDATE_FAILED | Se registra; no invalida por sí solo |
| 8 | POWER_FAILED | Se registra; se intenta borrar |
| 9 | Reservado | Sin nombre en el código |
| 10 | RH_ERROR | Se registra; no invalida por sí solo |
| 11 | DESICCANT_WARNING | Mantenimiento; no invalida por sí solo |
| 12 | BODY_TEMP_ERROR | Invalida |

Significados: SMP §15.6 p. 48; detalles §15.8 pp. 50–51. La selección de bits
que invalidan es una **política del proyecto** (`INVALID_DATA_MASK`), no toda
ella una instrucción literal del fabricante. `valid=1` exige además respuesta,
modo normal y magnitud principal disponible; no certifica calibración ni
compatibilidad del firmware. Hoy la comprobación física de Ld↓ no forma parte
de `Sample.valid`.

Ejemplo: `0x0108` tiene activos los bits 3 y 8. Son habituales tras un arranque,
pero ERROR también puede indicar otros fallos; no se ignora si persiste.

El programa escribe `true` en **coil 10, IO_CLEAR_ERROR**, mediante FC05.
Fuente: SMP §15.5 p. 46 y §15.9 p. 51. Se intenta al iniciar y, para ciertos
flags, con al menos 30 s de separación: ese intervalo es del proyecto. Borrar
un aviso no repara un fallo fatal ni recalibra el instrumento.

## 7. Comprobación física del SGR4

[SGR §7.1.5 p. 17](../references/pyrgeometer-SGR4/Operational_Manual_CGR4_SGR4_V10.pdf#page=17):

`Ld↓ = neta + 5,67 × 10⁻⁸ × (temperatura_cuerpo_°C + 273,15)⁴`.

El programa compara el registro 10 convertido con el cálculo de los registros
5 y 8. ±5 W/m² es un umbral del proyecto, **no una tolerancia de calibración
oficial**. Una coincidencia respalda la consistencia, pero no demuestra todo
el mapa ni la exactitud absoluta: ambas vías comparten datos del equipo. Una
diferencia exige investigar firmware, escala, tiempos y temperatura.

Con un cuerpo a 25 °C y neta −160 W/m² resulta Ld↓ ≈288 W/m². Es un ejemplo,
no una reconstrucción medida de la prueba 01 ni una prueba de cielo despejado.
Si el sensor apunta al suelo, cambia la interpretación geométrica de la
radiación incidente.

SGR §8.1 p. 24 confirma 19200 baud, paridad par, 8 bits y 1 stop, dirección de
fábrica 1. SGR §13.1 p. 29 indica respuesta 63 % <6 s, 95 % <18 s y espectro
4500–42000 nm. Leer cada segundo no hace que la respuesta física sea de 1 s.
El cableado está en SGR §7.2.2.5 p. 23.

## 8. Qué se conserva para reproducir una conversión

`sensor.py` convierte a `Sample`; `acquisition.py` confirma el ciclo en SQLite
y después escribe las proyecciones CSV con valores físicos.
`raw_irradiance_wm2` **no contiene palabras Modbus originales**. `scale_factor`
existe en memoria pero no está en `RAW_COLUMNS`. La hora la toma el PC tras
cada lectura; consultar en serie introduce desfase entre sensores del mismo
`cycle`. Un ciclo no implica adquisición simultánea.

SQLite conserva `raw_registers`, factor, `conversion_version`, modelo de datos
e identificación. Las lecturas fallidas conservan error y valores ausentes;
no se sustituyen por cero ni se interpolan. No se pueden reconstruir las
palabras de los CSV antiguos que nunca las guardaron.
