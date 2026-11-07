<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { live, useLive } from '$lib/live.svelte';
  import { colorFor, format, ROLE_NAMES } from '$lib/data';

  let { children } = $props();
  const links = [
    { href: '/', label: 'Monitor', icon: '◉' },
    { href: '/historico/', label: 'Histórico y descargas', icon: '▤' },
    { href: '/instrumentos/', label: 'Instrumentos', icon: '⚙' },
    { href: '/bitacora/', label: 'Bitácora y eventos', icon: '✎' },
    { href: '/documentacion/', label: 'Documentación', icon: '?' }
  ];
  let config = $derived(live.data?.config);
  let latest = $derived(live.data?.latest ?? []);
  let addresses = $derived([...new Set([...(config?.instruments.map(i => i.address) ?? []), ...latest.map(s => s.address)])].sort((a, b) => a - b));
  let clock = $derived(live.data?.clock);
  const roleOf = (address: number) => latest.find(s => s.address === address)?.role;
  const labelOf = (address: number) => config?.instruments.find(i => i.address === address)?.label || `#${address}`;
  const active = (href: string) => href === '/' ? page.url.pathname === '/' : page.url.pathname.startsWith(href);

  onMount(() => useLive(2000));
</script>

<div class="app">
  <aside>
    <a class="brand" href="/"><span class="brand-icon">☼</span><div>RADIOMETRÍA<small>OBSERVATORIO LOCAL</small></div></a>
    <nav aria-label="Secciones">
      {#each links as link}<a href={link.href} class:active={active(link.href)} aria-current={active(link.href) ? 'page' : undefined}><span>{link.icon}</span>{link.label}</a>{/each}
    </nav>
    <div class="equipment">
      <div class="section-label">INSTRUMENTOS</div>
      {#each addresses as address}
        {@const role = roleOf(address)}
        <p><i style:background={colorFor(address, addresses)}></i>{labelOf(address)}<small>{role ? ROLE_NAMES[role] : 'sin lecturas'}</small></p>
      {:else}<p class="muted">Sin instrumentos todavía</p>{/each}
    </div>
    <div class="status">
      <a href="/instrumentos/" class="pill" class:on={config?.solys2_in_use}>SOLYS2 · {config?.solys2_in_use ? 'en uso' : 'inactivo'}</a>
      <span class="pill" class:warn={!!clock?.warning} class:on={!!clock?.check?.ok && !clock?.warning}
            title={clock?.warning ?? (clock?.check ? `Servidor ${clock.check.server}` : '')}>
        Reloj · {clock?.check?.ok ? `${format(clock.check.offset_s, 3)} s` : clock?.enabled ? 'sin referencia' : 'sin verificar'}
      </span>
    </div>
    <div class="local"><span>●</span> Datos en este equipo<small>SQLite + Parquet · sin servicios externos</small></div>
  </aside>
  <main>{@render children()}</main>
</div>

<style>
  :global(*) { box-sizing: border-box; }
  :global(body) { margin: 0; font-family: Inter, ui-sans-serif, system-ui, sans-serif; background: #f3f6f5; color: #223d44; font-size: 13px; }
  :global(button), :global(select), :global(input), :global(textarea) { font: inherit; }
  :global(:focus-visible) { outline: 2px solid #3a9b8c; outline-offset: 3px; }
  :global(h1) { font-size: 27px; font-weight: 550; letter-spacing: -.8px; margin: 6px 0; }
  :global(.page-head) { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; padding: 28px 0 20px; border-bottom: 1px solid #dfe7e5; margin-bottom: 20px; flex-wrap: wrap; }
  :global(.page-head p) { color: #6f8589; font-size: 12px; margin: 0; max-width: 680px; line-height: 1.5; }
  :global(.eyebrow) { color: #82958f; font-size: 9px; letter-spacing: 1.8px; }
  :global(.notice) { border: 1px solid #e0d5b2; background: #fff9e9; color: #7a6a3a; border-radius: 6px; padding: 10px 14px; margin-bottom: 12px; font-size: 12px; }
  :global(.notice.error) { background: #fff2ef; border-color: #efd7d0; color: #a25f4e; }
  :global(.notice.ok) { background: #eef7f1; border-color: #cfe6d6; color: #3f7656; }
  :global(.notice small) { display: block; margin-top: 4px; font-family: monospace; }
  :global(.card) { background: #fff; border: 1px solid #dce3e3; border-radius: 9px; padding: 16px; }
  :global(.section-heading) { display: flex; align-items: center; justify-content: space-between; gap: 15px; padding: 24px 0 12px; flex-wrap: wrap; }
  :global(.section-heading h2) { font-size: 14px; font-weight: 600; margin: 0; }
  :global(.section-heading span) { font-size: 10px; color: #7f9295; }
  :global(.btn) { border: 1px solid #c9d8d4; background: #fff; color: #2f5550; border-radius: 6px; padding: 7px 12px; cursor: pointer; font-size: 12px; text-decoration: none; display: inline-flex; gap: 6px; align-items: center; }
  :global(.btn.primary) { background: #24584f; border-color: #24584f; color: #fff; }
  :global(.btn:disabled) { opacity: .5; cursor: default; }
  :global(label.field) { display: flex; flex-direction: column; gap: 5px; font-size: 11px; color: #5d7477; }
  :global(label.field input), :global(label.field select), :global(label.field textarea) { border: 1px solid #d3dedb; border-radius: 6px; padding: 7px 9px; background: #fff; color: #223d44; font-size: 12px; }
  :global(.qc) { border-radius: 4px; padding: 3px 7px; font-size: 9px; letter-spacing: .4px; background: #eef3f2; color: #66797c; white-space: nowrap; }
  :global(.qc.ok) { background: #e7f3ea; color: #3d7a52; }
  :global(.qc.suspect) { background: #fff1d6; color: #8f6c25; }
  :global(.qc.bad) { background: #fde6e1; color: #a2493a; }
  :global(table) { width: 100%; border-collapse: collapse; font-size: 11px; text-align: left; }
  :global(th) { color: #81969b; font-size: 9px; font-weight: 500; background: #f9fbfa; padding: 10px 12px; letter-spacing: .3px; }
  :global(td) { padding: 11px 12px; border-top: 1px solid #edf1ef; color: #4d6669; font-variant-numeric: tabular-nums; vertical-align: top; }
  :global(.table-wrap) { border: 1px solid #dce3e3; border-radius: 8px; overflow: auto; background: white; }
  .app { display: flex; min-height: 100vh; }
  aside { width: 232px; background: #142a31; color: #e2eeee; padding: 28px 18px; position: fixed; inset: 0 auto 0 0; display: flex; flex-direction: column; gap: 22px; overflow-y: auto; }
  .brand { display: flex; gap: 12px; align-items: center; font-size: 13px; font-weight: 700; letter-spacing: 1.3px; color: inherit; text-decoration: none; }
  .brand-icon { font-size: 31px; color: #bdcf85; font-weight: 400; }
  .brand small { display: block; font-size: 8px; letter-spacing: 1.9px; color: #93a9ad; margin-top: 5px; }
  nav { display: flex; flex-direction: column; gap: 4px; }
  nav a { color: #b9cdcf; text-decoration: none; padding: 10px; border-radius: 6px; font-size: 12px; display: flex; gap: 9px; border: 1px solid transparent; }
  nav a span { width: 14px; color: #7fa49c; text-align: center; }
  nav a.active { background: #244149; border-color: #34535a; color: #fff; }
  nav a:hover { background: #1d363e; }
  .section-label { color: #759197; font-size: 9px; letter-spacing: 1.4px; margin-bottom: 12px; }
  .equipment { border-top: 1px solid #29434a; padding-top: 20px; }
  .equipment p { font-size: 12px; margin: 0 0 12px; }
  .equipment small { display: block; color: #819ca1; font-size: 10px; padding: 4px 0 0 15px; }
  .equipment i { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 8px; }
  .muted { color: #819ca1; }
  .status { display: flex; flex-direction: column; gap: 7px; }
  .pill { font-size: 10px; border: 1px solid #34535a; border-radius: 999px; padding: 5px 10px; color: #a9c0c2; text-decoration: none; }
  .pill.on { border-color: #4f8b6e; color: #bfe3cc; }
  .pill.warn { border-color: #9c7f3c; color: #f0d79b; }
  .local { margin-top: auto; font-size: 10px; color: #b8ced1; }
  .local > span { color: #80b49e; padding-right: 5px; }
  .local small { display: block; color: #67858c; font-size: 9px; margin-top: 6px; }
  main { margin-left: 232px; padding: 0 34px 40px; width: calc(100% - 232px); max-width: 1850px; }
  @media (max-width: 900px) {
    .app { flex-direction: column; }
    aside { position: static; width: 100%; padding: 14px 16px; gap: 12px; }
    nav { flex-direction: row; overflow-x: auto; }
    nav a { white-space: nowrap; }
    .equipment, .local { display: none; }
    .status { flex-direction: row; flex-wrap: wrap; }
    main { margin-left: 0; width: 100%; padding: 0 16px 30px; }
  }
</style>
