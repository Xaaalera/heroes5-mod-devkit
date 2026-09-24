import { test } from 'node:test';
import assert from 'node:assert/strict';
import { documentationCriteria, validateDocsReview } from './review-docs.mjs';

const validate = (review, files, score = 10) => validateDocsReview(review, files, score);

const report = () => ({
  reviewer: 'test-reviewer',
  files: ['docs/example.md'],
  criteria: Object.fromEntries(documentationCriteria.map((criterion) => [
    criterion, { verdict: 'PASS', evidence: 'Fixture evidence for validator testing only.' },
  ])),
  findings: [],
});

test('a complete reasoned review covers its changed files', () => {
  assert.doesNotThrow(() => validate(report(), ['docs/example.md']));
});
test('a score without an independent report fails', () => {
  assert.throws(() => validate(undefined, []), /identity/);
});
test('omitted changed files fail', () => {
  assert.throws(() => validate(report(), ['docs/missing.md']), /every changed/);
});
test('every criterion requires evidence and a passing disposition', () => {
  for (const criterion of documentationCriteria) {
    for (const replacement of [undefined, { verdict: 'PASS', evidence: '' }, { verdict: 'FAIL', evidence: 'Defect' }]) {
      const review = report();
      review.criteria[criterion] = replacement;
      assert.throws(() => validate(review, review.files), new RegExp(criterion));
    }
  }
});
test('major and blocker findings prevent push', () => {
  for (const severity of ['major', 'blocker']) {
    const review = report();
    review.findings.push({ severity, path: review.files[0], detail: 'Confirmed defect' });
    assert.throws(() => validate(review, review.files), /blocking/);
  }
});
test('draft advisories remain visible without blocking', () => {
  const review = report();
  review.findings.push({ severity: 'advisory', path: review.files[0], detail: 'Known draft; not certified.' });
  assert.doesNotThrow(() => validate(review, review.files));
});
test('findings cannot omit severity or reference an unreviewed path', () => {
  const review = report();
  review.findings = [{ severity: 'minor', path: 'unknown.md', detail: 'Defect' }];
  assert.throws(() => validate(review, review.files), /reviewed path/);
  delete review.findings;
  assert.throws(() => validate(review, review.files), /explicitly list/);
});

test('minor findings deduct points and cannot hide behind a passing score', () => {
  const review = report();
  review.findings = [{ severity: 'minor', path: review.files[0], detail: 'Concrete minor defect.' }];
  assert.doesNotThrow(() => validate(review, review.files, 9));
  assert.throws(() => validate(review, review.files, 10), /expected 9/);
  review.findings.push(...review.findings, ...review.findings);
  assert.throws(() => validate(review, review.files, 10), /expected 7/);
  assert.doesNotThrow(() => validate(review, review.files, 7));
});
