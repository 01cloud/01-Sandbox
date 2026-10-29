import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";
import { componentTagger } from "lovable-tagger";

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const rawBackendUrl = env.VITE_API_BASE_URL || 'http://localhost:8000';
  const backendUrl = rawBackendUrl.replace(/\/+$/, '');

  const isNgrok = backendUrl.includes('ngrok');
  const proxyHeaders = isNgrok ? { 'ngrok-skip-browser-warning': 'true' } : {};

  const commonProxyOptions = {
    target: backendUrl,
    changeOrigin: true,
    secure: false,
    headers: proxyHeaders,
    proxyTimeout: 900000,
    timeout: 900000,
    configure: (proxy: any) => {
      proxy.on('error', (err: any, _req: any, res: any) => {
        // Prevent unhandled socket hang-ups from crashing or logging unhandled error events
        if (res && !res.headersSent && typeof res.writeHead === 'function') {
          try {
            res.writeHead(502, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: 'Upstream connection error', detail: err.message }));
          } catch (_) {}
        }
      });
    },
  };

  return {
    server: {
      host: "::",
      port: 8080,
      allowedHosts: true,
      hmr: {
        overlay: false,
      },
      proxy: {
        '/v1': commonProxyOptions,
        '/health': commonProxyOptions,
        '/grafana': commonProxyOptions,
        '/config': commonProxyOptions,
        '/api': commonProxyOptions,
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
