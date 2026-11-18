import { nextJsConfig } from "@repo/eslint-config/next-js";

/** @type {import("eslint").Linter.Config[]} */
export default [
  {
    name: "realytics/generated-artifacts",
    ignores: [
      ".next/**",
      ".next-e2e/**",
      ".next-e2e-production/**",
      ".pytest_cache/**",
      "coverage/**",
      "playwright-report/**",
      "test-results/**",
    ],
  },
  ...nextJsConfig,
];
