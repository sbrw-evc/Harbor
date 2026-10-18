import { defineConfig, mergeConfig } from "vitest/config";
import vite from "./vite.config";

// Component tests in jsdom; `npm run test:coverage` writes coverage/lcov.info for SonarQube.
export default mergeConfig(vite, defineConfig({
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["src/test/setup.ts"],
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.test.{ts,tsx}", "src/test/**", "src/i18n/ru/**"],
      reporter: ["lcov", "text-summary"],
      reportsDirectory: "coverage",
    },
  },
}));
