import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Важно: base = "/" чтобы import.meta.env.BASE_URL корректно указывал корень.
// На Vercel root-директория = miniapp/, output = dist — зашито в настройках проекта.
export default defineConfig({
  base: "/",
  plugins: [react()],
  build: { outDir: "dist" },
  server: { port: 5173 }
});
