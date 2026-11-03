import assert from 'node:assert/strict';
import test from 'node:test';
import { vworldRequestUrl } from '../../apps/web/lib/vworld-request.ts';

test('VWorld requests stay on the fixed origin and use validated tile coordinates', () => {
  const url = vworldRequestUrl(new URLSearchParams('kind=base&z=10&x=700&y=400'), 'dummy/key');
  assert.equal(url.origin, 'https://api.vworld.kr');
  assert.equal(url.pathname, '/req/wmts/1.0.0/dummy%2Fkey/Base/10/400/700.png');
  for (const query of [
    'kind=base&z=1&x=2&y=0', 'kind=base&z=999&x=0&y=0',
    'kind=base&z=1&x=../secret&y=0', 'kind=base&z=1&x=https://evil.invalid&y=0',
    'kind=base&z=-1&x=0&y=0', 'kind=base&z=1&x=1e1&y=0',
  ]) assert.equal(vworldRequestUrl(new URLSearchParams(query), 'dummy'), null);
});

test('VWorld WMS accepts only supported layers and finite ordered bounding boxes', () => {
  const params = new URLSearchParams({ kind: 'wms', layers: 'lt_c_uq111', bbox: '100,200,300,400' });
  const url = vworldRequestUrl(params, 'dummy');
  assert.equal(url.origin, 'https://api.vworld.kr');
  assert.equal(url.searchParams.get('bbox'), '100,200,300,400');
  for (const bbox of ['NaN,0,1,2', '0,0,Infinity,2', '3,4,1,2', ',0,1,2', '0,0,999999999,1']) {
    params.set('bbox', bbox);
    assert.equal(vworldRequestUrl(params, 'dummy'), null);
  }
  params.set('bbox', '0,0,1,2');
  params.set('layers', 'https://evil.invalid');
  assert.equal(vworldRequestUrl(params, 'dummy'), null);
});
