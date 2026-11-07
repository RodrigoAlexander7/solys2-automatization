# Glosario

| Término | Significado |
|---|---|
| **GHI** | Irradiancia global horizontal: toda la onda corta sobre un plano horizontal |
| **DHI** | Irradiancia difusa horizontal: la global sin el haz directo del sol (piranómetro sombreado) |
| **DNI** | Irradiancia directa normal: el haz del sol sobre un plano perpendicular a él (pirheliómetro) |
| **Ld↓ (LWD)** | Radiación de onda larga descendente: infrarrojo emitido por la atmósfera y las nubes |
| **Radiación neta (SGR4)** | Balance del detector del pirgeómetro; normalmente negativa con cielo despejado |
| **Cenit, z** | Ángulo entre la vertical y el sol; 0° con el sol en el cenit, 90° en el horizonte |
| **μ0** | `max(cos z, 0)` |
| **Sa** | Irradiancia extraterrestre del día: 1361 W/m² corregida por distancia Tierra-Sol |
| **kt** | Índice de claridad `GHI / (Sa·μ0)`; cerca de 0,7–0,8 con cielo despejado |
| **BSRN** | Baseline Surface Radiation Network, red de referencia de la OMM; define pruebas de calidad |
| **QC** | Control de calidad: pruebas que marcan valores sospechosos sin modificarlos |
| **Ciclo** | Una ronda de lecturas a todos los sensores; secuencial, no simultánea |
| **Corrida** | Período continuo de adquisición, desde que se inicia hasta que se detiene |
| **Rol** | Qué mide un instrumento según su montaje (GHI, DHI, LWD o sombra no garantizada) |
| **Versión de configuración** | Estado del observatorio (sitio, SOLYS2, bola de sombra, umbrales) con fecha de vigencia |
| **Flag** | Bit de estado informado por el sensor (`VOID_DATA`, `DESICCANT_WARNING`, …) |
| **Modbus RTU / FC04** | Protocolo serie maestro-esclavo; FC04 lee registros de entrada |
| **RTT** | Tiempo de ida y vuelta de una consulta Modbus |
| **SNTP / NTP** | Protocolo para comparar el reloj del PC con un servidor de hora |
| **Parquet** | Formato de archivo por columnas, comprimido, para el histórico de 1 s |
| **Carpet plot** | Mapa de color con días en un eje y hora del día en el otro |
