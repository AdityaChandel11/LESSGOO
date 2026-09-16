import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Development mirrors production: the browser talks to one origin and /api is
// forwarded to the backend, so cookies, the cross-site check and the live
// event stream behave exactly as they will when hosted.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        // Keep the browser's Host header so the backend's same-origin check
        // compares like with like.
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    // Leaflet and React together are comfortably under this; warn if a future
    // dependency (a charting or maps SDK) bloats the bundle.
    chunkSizeWarningLimit: 700,
  },
});
