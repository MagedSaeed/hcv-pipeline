// @vitest-environment node
import { fileURLToPath } from 'node:url';
import { afterEach, expect, it } from 'vitest';
import { http, HttpResponse, passthrough } from 'msw';
import { createServer } from 'vite';
import { server } from '../../test/server';

// The dev server must hand OPTIONS to Django like any other /api request.
// Vite's default CORS middleware sits in front of the proxy and answers every
// OPTIONS itself with an empty 204, which left the repetitions help text (read
// from `OPTIONS /api/experiments/{id}/run/`) blank in the browser while every
// msw-backed page test still passed. This boots the dev server from the real
// vite.config.js; msw stands in for Django behind the proxy.

let vite;

afterEach(async () => {
  await vite?.close();
  vite = undefined;
});

it('forwards OPTIONS /api requests to Django instead of answering them itself', async () => {
  vite = await createServer({
    configFile: fileURLToPath(new URL('../../../vite.config.js', import.meta.url)),
    logLevel: 'silent',
    server: { host: '127.0.0.1', port: 0, hmr: false },
    optimizeDeps: { noDiscovery: true },
  });
  await vite.listen();
  const origin = `http://127.0.0.1:${vite.httpServer.address().port}`;
  server.use(
    http.all(`${origin}/*`, () => passthrough()),
    http.options('http://localhost:8000/api/experiments/77/run/', () =>
      HttpResponse.json({ actions: { POST: { n_repetitions: { help_text: 'Sent by Django.' } } } })
    )
  );

  // Browsers attach Origin to every OPTIONS request, same-origin included.
  const response = await fetch(`${origin}/api/experiments/77/run/`, { method: 'OPTIONS', headers: { Origin: origin } });

  expect(response.status).toBe(200);
  const body = await response.json();
  expect(body.actions.POST.n_repetitions.help_text).toBe('Sent by Django.');
}, 30_000);
