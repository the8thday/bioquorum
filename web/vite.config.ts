import { defineConfig } from "vite";

export default defineConfig({
  base: "/assets/",
  worker: {format:"es",rolldownOptions:{output:{entryFileNames:"chem-worker-[hash].js"}}},
  build: {
    license: {fileName:"viewer-licenses.md"},
    outDir: "../src/bioquorum/server/web",
    emptyOutDir: true,
    assetsInlineLimit: 0,
    rolldownOptions: {
      output: {
        entryFileNames: "app.js",
        chunkFileNames: "viewer-[hash].js",
        assetFileNames: asset => asset.names.some(name => name.endsWith(".css")) ? "app.css" : "[name][extname]",
      },
    },
  },
});
