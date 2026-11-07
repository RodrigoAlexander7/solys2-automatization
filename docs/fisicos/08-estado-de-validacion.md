# Estado de validación

Esta página separa lo comprobado con hardware de lo que solo se probó con el
simulador. Léala antes de sacar conclusiones científicas de los datos.

## Comprobado con el montaje real

- Comunicación con los tres equipos en el bus (prueba 01): direcciones 2, 3 y 4,
  identificación, series y data models.
- Lectura de irradiancia de los SMP22x en las direcciones 3 y 4.

## Probado solo con simulador y tests

- **Lectura del SGR4 como pirgeómetro.** El SGR4 del montaje reporta data model
  102 y el manual V20 describe el 108. No está confirmado que los registros
  10–11 (Ld↓) existan con ese significado en el data model 102. La comprobación
  `neta + σT⁴` frente a Ld↓ leída sirve justamente para verificarlo en el
  hardware (`python -m smp22x info`).
- Pruebas de calidad, sombreado, DNI estimada y comportamiento del SOLYS2: con
  un cielo simulado que sigue al sol, no con mediciones.
- Archivo Parquet, retención, comprobación del reloj y exportaciones: con bases
  de prueba.
- Rendimiento sostenido en la mini-PC Windows.

## Datos que no deben usarse todavía

- **Humedad y presión internas** (registros 16–17): la unidad de presión es
  contradictoria en el manual y su significado en el firmware antiguo no está
  confirmado.
- Códigos de modelo `611` (SGR4) y `607` (SMP22x): leídos en la prueba 01, no
  figuran en el manual V20.

## Decisiones del proyecto (no del fabricante)

- La máscara de flags que invalida una lectura.
- `complete` = al menos 80 % de muestras válidas en el minuto.
- Porcentaje de diferencia solo con media > 100 W/m².
- Tolerancia de 5 W/m² en la comprobación del SGR4.
- Prueba de sombreado (kt > 0,6; DHI/GHI > 0,8) y comparación entre iguales (5 %).
- Constante solar 1361 W/m² para los límites BSRN.

## Hardware recomendado para operación fija

El PMU485 es una herramienta de instalación y servicio. Para medición
permanente, el fabricante recomienda una fuente de 12 V y un conversor RS-485
con aislamiento galvánico, o el Smart Powered Hub.
