import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// "/__PREFIX__" is replaced by the Flask server with URL_PREFIX at request time.
export default defineConfig({
  base: "/__PREFIX__/",
  plugins: [react()],
  server: {
    proxy: { "/api-dev": { target: "http://localhost:5023", rewrite: (p) => p.replace(/^\/api-dev/, "") , ws: true } },
  },
});
