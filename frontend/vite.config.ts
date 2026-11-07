import { defineConfig } from 'vite';
import { sveltekit } from '@sveltejs/kit/vite';

export default defineConfig({
  plugins: [sveltekit()],
  server: {
    proxy: { '/api': 'http://127.0.0.1:8050', '/manuales': 'http://127.0.0.1:8050' },
    // La documentación para físicos vive en ../docs/fisicos y se compila en el build.
    fs: { allow: ['..'] }
  }
});
