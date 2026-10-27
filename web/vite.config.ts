import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The build lands inside the Python package so `pip install` + FastAPI serve the
// app without Node: Node is only needed to change the UI, not to run it.
const OUT_DIR = "../src/processpilot/api/static";

// API routes the dev server forwards to a running backend (python -m processpilot.api).
const API_PREFIXES = ["/health", "/presets", "/datasets", "/runs", "/jobs", "/decisions", "/settings"];
const API_URL = process.env.PP_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  build: { outDir: OUT_DIR, emptyOutDir: true, chunkSizeWarningLimit: 1200 },
  server: {
    port: 5173,
    proxy: Object.fromEntries(API_PREFIXES.map((p) => [p, { target: API_URL, changeOrigin: true }])),
  },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
