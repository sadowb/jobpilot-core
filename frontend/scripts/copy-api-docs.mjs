import { createRequire } from 'node:module';
import { copyFile, mkdir } from 'node:fs/promises';
import { dirname, join } from 'node:path';

const require = createRequire(import.meta.url);
const source = dirname(require.resolve('swagger-ui-dist/package.json'));
const destination = new URL('../dist/api-docs/', import.meta.url);
await mkdir(destination, { recursive: true });
for (const name of ['swagger-ui-bundle.js', 'swagger-ui.css']) {
  await copyFile(join(source, name), new URL(name, destination));
}
