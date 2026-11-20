import { defineConfig } from "vite";

export default defineConfig({
  base: "/assets/",
  build: {
    outDir: "../src/bioquorum/server/web",
    emptyOutDir: true,
    assetsInlineLimit: 0,
    rolldownOptions: {
      output: {
        entryFileNames: "app.js",
        assetFileNames: asset => asset.names.some(name => name.endsWith(".css")) ? "app.css" : "[name][extname]",
      },
    },
  },
});
