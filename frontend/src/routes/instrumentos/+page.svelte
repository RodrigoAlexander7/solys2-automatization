<script lang="ts">
  import { onMount } from 'svelte';
  import { get, post, type ApiError, type ConfigVersion, type ObservatoryConfig, type Sample } from '$lib/api';
  import { refreshLive, rememberAuthor, savedAuthor } from '$lib/live.svelte';
  import { colorFor, format, fromInputValue, localDate, QC_TEXT, ROLE_NAMES } from '$lib/data';

  type DeviceInfo = { model: string; serial_label: string; device_type: number; datamodel_version: number;
    software_version: number; hardware_version: number; node_id: number; batch_year: number; serial_number: number; warnings: string[] };
  type Item = { address: number; sensor_type: string; info: DeviceInfo | null; latest: Sample | null; role: string | null;
    stats: { requests?: number; responses_ok?: number; samples_valid?: number; comm_errors?: number } | null };
  type Inventory = { run: { id: string; state: string; started_utc: string; simulated: boolean; bus: string; label: string } | null; items: Item[] };
  type ConfigView = { current: ObservatoryConfig; current_version: number | null; versions: ConfigVersion[];
    sensor_types: Record<string, string>; qc_defaults: ObservatoryConfig['qc'] };

  let inventory = $state<Inventory | null>(null);
  let view = $state<ConfigView | null>(null);
  let draft = $state<ObservatoryConfig | null>(null);
  let siteEnabled = $state(false);
  let author = $state(''), note = $state(''), validFrom = $state('');
  let saving = $state(false), error = $state<ApiError | null>(null), saved = $state<string[] | null>(null);

  let addresses = $derived([...new Set([...(inventory?.items.map(i => i.address) ?? []), ...(draft?.instruments.map(i => i.address) ?? [])])].sort((a, b) => a - b));
  const typeOf = (address: number) => view?.sensor_types[String(address)] ?? inventory?.items.find(i => i.address === address)?.sensor_type ?? 'pyranometer';

  async function load() {
    const [inv, cfg] = await Promise.all([get<Inventory>('/api/instruments'), get<ConfigView>('/api/observatory/config')]);
    if (inv.ok) inventory = inv.value; else error = inv.error;
    if (cfg.ok) {
      view = cfg.value;
      const current = structuredClone(cfg.value.current);
      for (const address of [...new Set([...(inv.ok ? inv.value.items.map(i => i.address) : [])])])
        if (!current.instruments.some(i => i.address === address)) current.instruments.push({ address, shaded: false, label: '' });
      current.instruments.sort((a, b) => a.address - b.address);
      draft = current;
      siteEnabled = current.site !== null;
      if (!current.site) draft.site = { name: '', latitude: 0, longitude: 0, altitude_m: 0 };
    } else error = cfg.error;
  }

  async function save(event: SubmitEvent) {
    event.preventDefault();
    if (!draft) return;
    saving = true; error = null; saved = null;
    rememberAuthor(author);
    const config = { ...draft, site: siteEnabled ? draft.site : null };
    const result = await post<{ version: ConfigVersion; warnings: string[] }>('/api/observatory/config', {
      author, note, valid_from: validFrom ? new Date(fromInputValue(validFrom)!).toISOString() : null, config
    });
    saving = false;
    if (!result.ok) { error = result.error; return; }
    saved = result.value.warnings;
    note = ''; validFrom = '';
    refreshLive();
    await load();
  }

  async function toggleSolys2() {
    if (!draft) return;
    draft.solys2_in_use = !draft.solys2_in_use;
  }

  onMount(() => { author = savedAuthor(); void load(); });
</script>

<svelte:head><title>Instrumentos · Radiometría</title></svelte:head>

<section class="page-head">
  <div>
    <div class="eyebrow">INVENTARIO Y CONFIGURACIÓN</div>
    <h1>Instrumentos</h1>
    <p>Identificación leída de cada equipo y configuración del observatorio. Un cambio crea una versión nueva con fecha de vigencia y una entrada en la bitácora: los días anteriores se siguen interpretando con la configuración que tenían.</p>
  </div>
</section>

{#if error}<div class="notice error" role="alert"><strong>{error.code}</strong> · {error.message}{#if error.trace_id}<small>Traza: {error.trace_id}</small>{/if}</div>{/if}

<div class="section-heading"><h2>Equipos en el bus</h2><span>{inventory?.run ? `Corrida «${inventory.run.label || 'sin etiqueta'}» · ${inventory.run.simulated ? 'SIMULADO' : inventory.run.bus}` : 'Sin corridas todavía'}</span></div>
<div class="table-wrap"><table>
  <thead><tr><th>Dirección</th><th>Modelo</th><th>Serie</th><th>Firmware / hardware</th><th>Data model</th><th>IO_NODE_ID</th><th>Rol actual</th><th>Comunicación</th><th>Última lectura</th><th>Avisos</th></tr></thead>
  <tbody>
    {#each inventory?.items ?? [] as item (item.address)}
      <tr>
        <td><i class="dot" style:background={colorFor(item.address, addresses)}></i><strong>#{item.address}</strong></td>
        <td>{item.info?.model ?? 'sin identificar'}<small>{item.sensor_type === 'pyrgeometer' ? 'pirgeómetro' : 'piranómetro'}</small></td>
        <td>{item.info?.serial_label ?? '—'}</td>
        <td>{item.info ? `sw ${item.info.software_version} · hw ${item.info.hardware_version}` : '—'}</td>
        <td>{item.info?.datamodel_version ?? '—'}</td>
        <td>{item.info?.node_id ?? '—'}</td>
        <td>{item.role ? ROLE_NAMES[item.role as keyof typeof ROLE_NAMES] : '—'}</td>
        <td>{item.stats ? `${item.stats.responses_ok ?? 0}/${item.stats.requests ?? 0} respuestas · ${item.stats.comm_errors ?? 0} errores` : '—'}</td>
        <td>{#if item.latest}{format(item.latest.measurement)} W/m² <span class="qc {item.latest.qc.summary}">{QC_TEXT[item.latest.qc.summary]}</span><small>{localDate(item.latest.time_utc)}</small>{:else}—{/if}</td>
        <td class="warn">{#each item.info?.warnings ?? [] as w}<small>{w}</small>{:else}—{/each}</td>
      </tr>
    {:else}<tr><td colspan="10" class="empty">Sin instrumentos identificados todavía.</td></tr>{/each}
  </tbody>
</table></div>
<p class="hint">La dirección Modbus de cada equipo se cambia con SmartExplorer, con la adquisición detenida (manual SMP §8.1.3). Esta página no escribe en los sensores.</p>

{#if draft}
<form class="config" onsubmit={save}>
  <div class="section-heading"><h2>Configuración del observatorio</h2><span>{view?.current_version ? `Versión vigente v${view.current_version}` : 'Sin versiones: rige la configuración por defecto'}</span></div>

  <section class="card solys2" class:on={draft.solys2_in_use}>
    <div>
      <h3>Seguidor SOLYS2</h3>
      <p>{draft.solys2_in_use ? 'En uso: el piranómetro con bola de sombra mide la difusa (DHI). Se activan el cociente difusa/global, la prueba de sombreado y la DNI estimada.' :
        'Inactivo (por ejemplo, en mantenimiento): el piranómetro con bola se sigue registrando, pero no se presenta como difusa y se desactivan las funciones que dependen del seguidor.'}</p>
    </div>
    <button type="button" class="switch" role="switch" aria-checked={draft.solys2_in_use} aria-label="SOLYS2 en uso" onclick={toggleSolys2}><span></span></button>
  </section>

  <section class="card">
    <h3>Instrumentos</h3>
    <div class="table-wrap inner"><table>
      <thead><tr><th>Dirección</th><th>Tipo</th><th>Bola de sombra</th><th>Etiqueta</th><th>Rol resultante</th></tr></thead>
      <tbody>
        {#each draft.instruments as inst (inst.address)}
          {@const pyr = typeOf(inst.address) === 'pyrgeometer'}
          <tr>
            <td><strong>#{inst.address}</strong></td>
            <td>{pyr ? 'Pirgeómetro' : 'Piranómetro'}</td>
            <td><label class="check"><input type="checkbox" bind:checked={inst.shaded} disabled={pyr} /> {pyr ? 'no cambia la magnitud' : inst.shaded ? 'Sí' : 'No'}</label></td>
            <td><input class="text" maxlength="60" bind:value={inst.label} placeholder="p. ej. SMP22 norte" /></td>
            <td>{pyr ? ROLE_NAMES.LWD : inst.shaded ? (draft.solys2_in_use ? ROLE_NAMES.DHI : ROLE_NAMES.SHADE_OFF) : ROLE_NAMES.GHI}</td>
          </tr>
        {/each}
      </tbody>
    </table></div>
  </section>

  <section class="card">
    <h3>Sitio <small>(necesario para la posición solar y las pruebas BSRN; puede tomarse del GPS del SOLYS2)</small></h3>
    <label class="check"><input type="checkbox" bind:checked={siteEnabled} /> Sitio configurado</label>
    {#if siteEnabled && draft.site}
      <div class="grid">
        <label class="field">Nombre<input maxlength="80" bind:value={draft.site.name} /></label>
        <label class="field">Latitud (°, + norte)<input type="number" step="0.000001" min="-90" max="90" bind:value={draft.site.latitude} required /></label>
        <label class="field">Longitud (°, + este)<input type="number" step="0.000001" min="-180" max="180" bind:value={draft.site.longitude} required /></label>
        <label class="field">Altura (m)<input type="number" step="0.1" min="-500" max="9000" bind:value={draft.site.altitude_m} /></label>
      </div>
    {/if}
  </section>

  <section class="card">
    <h3>Umbrales del proyecto <small>(no son límites del fabricante ni de BSRN)</small></h3>
    <div class="grid">
      <label class="field">Comparación entre iguales: diferencia máxima (%)<input type="number" step="0.1" min="0.1" max="100" bind:value={draft.qc.comparison_pct} /></label>
      <label class="field">Sombreado: índice de claridad mínimo (kt)<input type="number" step="0.01" min="0.01" max="1.49" bind:value={draft.qc.shading_kt_min} /></label>
      <label class="field">Sombreado: DHI/GHI máximo<input type="number" step="0.01" min="0.01" max="1.5" bind:value={draft.qc.shading_ratio_max} /></label>
    </div>
  </section>

  <section class="card">
    <h3>Registrar el cambio</h3>
    <div class="grid">
      <label class="field">Autor<input required maxlength="60" bind:value={author} placeholder="Nombre" /></label>
      <label class="field">Vigente desde (vacío = ahora)<input type="datetime-local" bind:value={validFrom} /></label>
      <label class="field wide">Nota (obligatoria si la vigencia es retroactiva)<textarea rows="2" maxlength="500" bind:value={note} placeholder="Motivo del cambio"></textarea></label>
    </div>
    <div class="actions"><button class="btn primary" type="submit" disabled={saving || !author.trim()}>{saving ? 'Guardando…' : 'Guardar nueva versión'}</button></div>
    {#if saved}<div class="notice ok" role="status">Versión guardada.{#each saved as w}<small>Aviso: {w}</small>{/each}</div>{/if}
  </section>
</form>
{/if}

<div class="section-heading"><h2>Historial de configuraciones</h2><span>Solo se agregan versiones; ninguna se borra</span></div>
<div class="table-wrap"><table>
  <thead><tr><th>Versión</th><th>Vigente desde</th><th>Autor</th><th>SOLYS2</th><th>Con bola de sombra</th><th>Sitio</th><th>Nota</th></tr></thead>
  <tbody>
    {#each view?.versions ?? [] as v (v.id)}
      <tr>
        <td>v{v.id}{#if v.id === view?.current_version} <span class="qc ok">vigente</span>{/if}</td>
        <td>{localDate(v.valid_from)}<small>creada {localDate(v.created)}</small></td>
        <td>{v.author}</td>
        <td>{v.config.solys2_in_use ? 'en uso' : 'inactivo'}</td>
        <td>{v.config.instruments.filter(i => i.shaded).map(i => `#${i.address}`).join(', ') || '—'}</td>
        <td>{v.config.site ? `${format(v.config.site.latitude, 4)}, ${format(v.config.site.longitude, 4)}` : 'sin sitio'}</td>
        <td>{v.note || '—'}</td>
      </tr>
    {:else}<tr><td colspan="7" class="empty">Todavía no hay versiones guardadas.</td></tr>{/each}
  </tbody>
</table></div>

<style>
  td small { display: block; color: #7d9093; font-size: 9px; margin-top: 4px; }
  .warn small { color: #9a7b41; max-width: 240px; white-space: normal; }
  .dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 6px; }
  .empty { text-align: center; padding: 24px; }
  .hint { font-size: 11px; color: #73888b; margin: 8px 0 0; }
  .config { display: flex; flex-direction: column; gap: 12px; }
  .config h3 { margin: 0 0 10px; font-size: 13px; font-weight: 600; }
  .config h3 small { font-weight: 400; color: #7d9093; font-size: 11px; }
  .solys2 { display: flex; justify-content: space-between; gap: 20px; align-items: center; }
  .solys2 p { margin: 0; font-size: 12px; color: #61787b; max-width: 760px; line-height: 1.5; }
  .solys2.on { border-color: #b9dcc8; background: #f3faf5; }
  .switch { width: 52px; height: 28px; border-radius: 999px; border: 1px solid #c3d0cd; background: #e5ecea; position: relative; cursor: pointer; flex: none; }
  .switch span { position: absolute; top: 3px; left: 3px; width: 20px; height: 20px; border-radius: 50%; background: #fff; box-shadow: 0 1px 3px #0003; transition: left .15s; }
  .switch[aria-checked='true'] { background: #2f7a5f; border-color: #2f7a5f; }
  .switch[aria-checked='true'] span { left: 27px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin-top: 8px; }
  .wide { grid-column: 1 / -1; }
  .check { display: flex; gap: 6px; align-items: center; font-size: 12px; }
  .text { border: 1px solid #d3dedb; border-radius: 5px; padding: 5px 8px; width: 100%; min-width: 140px; }
  .inner { border: none; }
  .actions { margin-top: 12px; }
</style>
