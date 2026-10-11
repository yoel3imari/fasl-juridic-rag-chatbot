import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // Generated / non-source output:
    "coverage/**",
    "playwright-report/**",
    "test-results/**",
  ]),
  {
    rules: {
      // `_`-prefixed args/vars are intentional placeholders (e.g. dropping a
      // key via rest destructuring). Keep them silent without hiding real dead
      // code, which stays reported.
      "@typescript-eslint/no-unused-vars": [
        "warn",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
          ignoreRestSiblings: true,
        },
      ],
      // New in eslint-plugin-react-hooks v6 (React Compiler era). It flags the
      // "reset / sync local state when a prop or `open` changes" effect pattern
      // used deliberately across the app's modals and panels. Kept as a warning
      // so the signal stays visible while those effects are migrated to
      // event-driven resets (see https://react.dev/learn/you-might-not-need-an-effect).
      "react-hooks/set-state-in-effect": "warn",
    },
  },
]);

export default eslintConfig;
