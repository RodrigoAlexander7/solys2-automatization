<script lang="ts">
  import { onMount } from 'svelte';
  import { docs } from '$lib/docs';

  let slug = $state(docs[0]?.slug ?? '');
  let current = $derived(docs.find(d => d.slug === slug) ?? docs[0]);

  function sync() {
    const hash = decodeURIComponent(location.hash.slice(1));
    if (docs.some(d => d.slug === hash)) { slug = hash; scrollTo({ top: 0 }); }
  }

  onMount(() => {
    sync();
    addEventListener('hashchange', sync);
    return () => removeEventListener('hashchange', sync);
  });
</script>

<svelte:head><title>Documentación · Radiometría</title></svelte:head>

<section class="page-head">
  <div>
    <div class="eyebrow">PARA QUIENES MIDEN</div>
    <h1>Documentación</h1>
    <p>Qué mide cada instrumento, cómo se convierten y validan los datos, qué significan las pruebas de calidad y cómo operar el observatorio. Los enlaces a manuales abren el PDF local en la página citada.</p>
  </div>
</section>

<div class="docs">
  <nav aria-label="Índice de documentación">
    {#each docs as doc}<a href={`#${doc.slug}`} class:active={doc.slug === current?.slug} aria-current={doc.slug === current?.slug ? 'page' : undefined}>{doc.title}</a>{/each}
  </nav>
  {#if current}
    <!-- Contenido propio del repositorio (docs/fisicos), compilado en el build. -->
    <article class="prose">{@html current.html}</article>
  {:else}<div class="notice">No se encontraron documentos.</div>{/if}
</div>

<style>
  .docs { display: grid; grid-template-columns: 240px minmax(0, 1fr); gap: 24px; align-items: start; }
  nav { position: sticky; top: 16px; display: flex; flex-direction: column; gap: 2px; background: #fff; border: 1px solid #dce3e3; border-radius: 9px; padding: 8px; }
  nav a { color: #476064; text-decoration: none; padding: 8px 10px; border-radius: 6px; font-size: 12px; }
  nav a.active { background: #e8f1ee; color: #1f4c45; font-weight: 600; }
  .prose { background: #fff; border: 1px solid #dce3e3; border-radius: 9px; padding: 26px 32px; max-width: 900px; line-height: 1.65; font-size: 13.5px; color: #2c464b; }
  .prose :global(h1) { font-size: 22px; margin-top: 0; }
  .prose :global(h2) { font-size: 16px; margin-top: 26px; border-bottom: 1px solid #edf1ef; padding-bottom: 6px; }
  .prose :global(h3) { font-size: 14px; margin-top: 20px; }
  .prose :global(code) { background: #f1f5f4; border-radius: 4px; padding: 1px 5px; font-size: 12px; }
  .prose :global(pre) { background: #f4f7f6; border: 1px solid #e3eae8; border-radius: 6px; padding: 12px; overflow-x: auto; }
  .prose :global(table) { margin: 12px 0; border: 1px solid #e1e8e6; }
  .prose :global(th), .prose :global(td) { border: 1px solid #e8eeec; padding: 7px 10px; font-size: 12px; }
  .prose :global(blockquote) { margin: 12px 0; padding: 8px 14px; border-left: 3px solid #c9952f; background: #fffbf2; color: #6f5a2a; }
  .prose :global(a) { color: #2a6f62; }
  @media (max-width: 900px) { .docs { grid-template-columns: 1fr; } nav { position: static; } .prose { padding: 18px; } }
</style>
