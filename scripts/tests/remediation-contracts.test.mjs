import test from 'node:test';
import assert from 'node:assert/strict';
import { projectIdFromLocation, withProjectContext } from '../../apps/web/lib/project-navigation.ts';
import { intakeProcessingFailure } from '../../apps/web/lib/intake-processing-status.ts';
import { trancheAmounts, amountInEok } from '../../apps/web/lib/tranche-display.ts';

test('URL project context is authoritative over a conflicting query', () => {
  assert.equal(projectIdFromLocation('/project/751/cashflow', '?projectId=748'), '751');
  assert.equal(projectIdFromLocation('/cashflow-detail', '?projectId=751'), '751');
  assert.equal(withProjectContext('/cashflow-detail', '751'), '/cashflow-detail?projectId=751');
});
test('invalid project paths and external links are not rewritten', () => {
  assert.equal(projectIdFromLocation('/project/%2Fetc', ''), null);
  assert.equal(withProjectContext('https://example.com', '751'), 'https://example.com');
  assert.equal(withProjectContext('//example.com', '751'), '//example.com');
});
test('HTTP success does not hide extraction or validation failures', () => {
  for (const body of [{status: 'host_llm_required'}, {validation: {status: 'parse_failed'}},
    {project: {_meta: {parsing_source: 'extraction_unavailable'}}}]) {
    assert.ok(intakeProcessingFailure(body));
  }
  assert.equal(intakeProcessingFailure({status: 'completed'}), null);
  assert.match(intakeProcessingFailure({autoReanalysis:{triggered:true,cascadeComplete:false}}), /재분석/);
  assert.equal(intakeProcessingFailure({autoReanalysis:{triggered:false}}), null);
});
test('tranche uses engine amounts, missing values remain missing', () => {
  assert.deepEqual(trancheAmounts({claimAmount: 107, recoveredAmount: 80, shortfall: 27, recoveryRatio: .7477}),
    {claimAmount: 107, recoveredAmount: 80, shortfall: 27, recoveryRatio: .7477});
  assert.deepEqual(trancheAmounts({principalClaim: 100}),
    {claimAmount: null, recoveredAmount: null, shortfall: null, recoveryRatio: null});
  assert.equal(amountInEok(null), null);
  assert.equal(amountInEok(0), 0);
});
