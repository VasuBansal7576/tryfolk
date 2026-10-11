import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const target = env.TRYFOLK_API_PROXY || 'http://127.0.0.1:8000';
  return {
    server: {
      host: '127.0.0.1',
      proxy: {
        '^/(quotes|runs|health|readiness|owner|connections|api)(/|$)': { target, changeOrigin: true },
      },
    },
  };
});
