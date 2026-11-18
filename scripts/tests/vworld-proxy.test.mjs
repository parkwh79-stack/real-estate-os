import assert from 'node:assert/strict';
import test from 'node:test';
import { fetchVworldTile } from '../../apps/web/lib/vworld-proxy.ts';
import { vworldRequestUrl } from '../../apps/web/lib/vworld-request.ts';

const request = { origin: 'https://preview.example', searchParams: new URLSearchParams('kind=base&z=1&x=1&y=0') };
const environment = { VWORLD_API_KEY: ' fixture-only\r\n ', REALYTICS_WEB_ORIGIN: 'https://realytics.example/app' };

test('image proxy trims credentials and uses the configured service referer', async () => {
  const response = await fetchVworldTile(request, environment, vworldRequestUrl, async (url, options) => {
    assert.equal(url.pathname, '/req/wmts/1.0.0/fixture-only/Base/1/0/1.png');
    assert.equal(options.headers.Referer, 'https://realytics.example/');
    assert.equal(options.redirect, 'error');
    return new Response(new Uint8Array([1, 2, 3]), { headers: { 'content-type': 'image/png' } });
  });
  assert.equal(response.status, 200);
  assert.deepEqual([...new Uint8Array(await response.arrayBuffer())], [1, 2, 3]);
});

test('blank server key falls back to a trimmed browser key', async () => {
  const response = await fetchVworldTile(request, { VWORLD_API_KEY: '\n ', NEXT_PUBLIC_VWORLD_API_KEY: ' fixture-browser\n' }, vworldRequestUrl, async (url) => {
    assert.equal(url.pathname.includes('/fixture-browser/'), true);
    return new Response('image', { headers: { 'content-type': 'image/png' } });
  });
  assert.equal(response.status, 200);
});

test('missing configuration and invalid requests do not fetch or cache', async () => {
  const forbiddenFetch = async () => { throw new Error('must not fetch'); };
  for (const [req, env, status] of [[request, {}, 503], [{ ...request, searchParams: new URLSearchParams('kind=base&z=1&x=9&y=0') }, environment, 400]]) {
    const response = await fetchVworldTile(req, env, vworldRequestUrl, forbiddenFetch);
    assert.equal(response.status, status);
    assert.equal(response.headers.get('cache-control'), 'no-store');
  }
});

test('provider errors and XML never become successful transparent image tiles', async () => {
  for (const [upstreamStatus, expected] of [[200, 502], [403, 502], [429, 503], [503, 503], [504, 504]]) {
    const response = await fetchVworldTile(request, environment, vworldRequestUrl, async () => new Response('<error>fixture-only</error>', {
      status: upstreamStatus, headers: { 'content-type': 'application/xml' },
    }));
    assert.equal(response.status, expected);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.equal(response.headers.get('content-type').includes('application/json'), true);
    const payload = await response.text();
    assert.equal(payload.includes('fixture-only'), false);
    assert.equal(JSON.parse(payload).upstreamStatus, upstreamStatus);
  }
});

test('network and timeout failures are safe non-cached errors', async () => {
  for (const [name, status] of [['TimeoutError', 504], ['AbortError', 504], ['TypeError', 502]]) {
    const response = await fetchVworldTile(request, environment, vworldRequestUrl, async () => { const error = new Error('sensitive-url'); error.name = name; throw error; });
    assert.equal(response.status, status);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.equal((await response.text()).includes('sensitive-url'), false);
  }
});
