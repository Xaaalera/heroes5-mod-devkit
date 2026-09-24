export const documentationCriteria = [
  'purpose', 'structure', 'specificity', 'reproducibility',
  'evidence', 'applicability', 'translations', 'maintenance',
];

export const validateDocsReview = (review, files, score) => {
  if (!review || typeof review.reviewer !== 'string' || !review.reviewer.trim()) {
    throw new Error('Docs review requires the actual independent reviewer identity.');
  }
  if (!Array.isArray(review.files) || files.some((file) => !review.files.includes(file))) {
    throw new Error('Docs review must cover every changed Markdown file.');
  }
  for (const criterion of documentationCriteria) {
    const result = review.criteria?.[criterion];
    if (!result || !['PASS', 'N/A'].includes(result.verdict)
        || typeof result.evidence !== 'string' || !result.evidence.trim()) {
      throw new Error(`Docs review requires a reasoned PASS or N/A for ${criterion}.`);
    }
  }
  if (!Array.isArray(review.findings)) {
    throw new Error('Docs review must explicitly list findings, including an empty list.');
  }
  for (const finding of review.findings) {
    if (!['blocker', 'major', 'minor', 'advisory'].includes(finding.severity)
        || typeof finding.path !== 'string' || !review.files.includes(finding.path)
        || typeof finding.detail !== 'string' || !finding.detail.trim()) {
      throw new Error('Docs review finding requires severity, reviewed path and concrete detail.');
    }
    if (['blocker', 'major'].includes(finding.severity)) {
      throw new Error('Docs review has blocking findings. Fix and review the final diff again.');
    }
  }
  const expectedScore = Math.max(0, 10 - 3 * review.findings.filter((finding) => finding.severity === 'major').length
    - 20 * review.findings.filter((finding) => finding.severity === 'blocker').length
    - review.findings.filter((finding) => finding.severity === 'minor').length);
  if (score !== expectedScore) {
    throw new Error(`Docs score must match its findings: expected ${expectedScore}.`);
  }
};
