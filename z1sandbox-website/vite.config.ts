import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";
import { componentTagger } from "lovable-tagger";

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd());
  const backendUrl = env.VITE_API_BASE_URL || 'https://contests-name-publishers-off.trycloudflare.com';

  const isNgrok = backendUrl.includes('ngrok');
  const proxyHeaders = isNgrok ? { 'ngrok-skip-browser-warning': 'true' } : {};

  return {
    server: {
      host: "::",
      port: 8080,
      allowedHosts: true,
      hmr: {
        overlay: false,
      },
      proxy: {
        '/v1': {
          target: backendUrl,
          changeOrigin: true,
          headers: proxyHeaders
        },
        '/config': {
          target: backendUrl,
          changeOrigin: true,
          headers: proxyHeaders
        },
        '/api': {
          target: backendUrl,
          changeOrigin: true,
          headers: proxyHeaders
        },
      }
    },
    plugins: [react(), mode === "development" && componentTagger()].filter(Boolean),
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
      dedupe: ["react", "react-dom", "react/jsx-runtime", "react/jsx-dev-runtime", "@tanstack/react-query", "@tanstack/query-core"],
    },
  };
});
