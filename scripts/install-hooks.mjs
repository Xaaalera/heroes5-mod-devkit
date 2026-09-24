import { spawnSync, execFileSync } from 'node:child_process';

const configured = spawnSync('git', ['config', '--get', 'core.hooksPath'], { encoding: 'utf8' });
if (configured.status !== 0 && configured.status !== 1) {
  throw new Error('Cannot read Git hook configuration.');
}
const current = configured.stdout.trim();
if (current && current !== '.githooks') {
  throw new Error(`Existing hooksPath (${current}) preserved. Integrate .githooks/pre-push before pushing.`);
}
execFileSync('git', ['config', '--local', 'core.hooksPath', '.githooks']);
console.log('Installed the mandatory pre-push review gate.');
