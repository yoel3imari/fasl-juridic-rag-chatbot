// Docker prod-builder config only (see Dockerfile `builder` stage): wraps the
// base Next.js config with `output: 'standalone'` to shrink the runner image.
// Local `next dev` / `next build` / `next start` never pick this file up (Next
// only loads next.config.{js,mjs,cjs,ts,...}), so `npm run build` and
// `npm run start` keep working with zero changes to package.json.
// NOTE: inside the Docker builder, next.config.js is renamed to
// next.config.base.js and this file is copied to next.config.mjs before
// `npm run build`, which is why the import below points at the base name.
import baseConfig from './next.config.base.js';

export default {
  ...baseConfig,
  output: 'standalone',
};
