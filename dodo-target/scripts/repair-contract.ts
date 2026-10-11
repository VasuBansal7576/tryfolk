import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { lstatSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { z } from 'zod';
import { sourceFingerprint } from '../lib/config';

const seam='lib/fulfillment-policy.ts';
const commands=['npm run test:repair','npm run typecheck','npm run build','npm run smoke'];
const sha=(bytes:Buffer|string)=>createHash('sha256').update(bytes).digest('hex');
const File=z.strictObject({path:z.string(),sha256:z.string().regex(/^[a-f0-9]{64}$/)});
const Contract=z.strictObject({
  schema:z.literal('tryfolk.dodo-repair.v1'),source_commit:z.string().regex(/^[a-f0-9]{40}$/),source_clean:z.boolean(),
  source_sha256:z.string().regex(/^[a-f0-9]{64}$/),allowed_source_files:z.array(z.string()),
  seam_sha256:z.string().regex(/^[a-f0-9]{64}$/),protected_files_sha256:z.string().regex(/^[a-f0-9]{64}$/),
  export_files:z.array(File),regression_commands:z.array(z.string()),
});
function git(args:string[]) {
  return execFileSync('git',args,{encoding:'utf8',stdio:['ignore','pipe','ignore']}).trim();
}
function snapshot() {
  const names=git(['ls-files','-z','--','.']).split('\0').filter(Boolean)
    .filter(path=>!path.split('/').some(part=>part.startsWith('.env') || ['upstream','.data','.next','node_modules','credentials','secrets'].includes(part)))
    .filter(path=>!path.endsWith('.tsbuildinfo') && !/\.sqlite(?:-shm|-wal)?$/.test(path) && !['.target-manifest.json','lib/source-fingerprint.json'].includes(path))
    .sort();
  if (!names.includes(seam)) throw new Error('repair_seam_not_tracked: commit the owned source before exporting');
  const export_files=names.map(path=>{
    if (path.startsWith('/') || path.split('/').includes('..') || !lstatSync(path).isFile()) throw new Error('unsafe_export_file');
    return {path,sha256:sha(readFileSync(path))};
  });
  const protectedFiles=export_files.filter(file=>file.path !== seam);
  return Contract.parse({schema:'tryfolk.dodo-repair.v1',source_commit:git(['rev-parse','HEAD']),
    source_clean:git(['status','--porcelain','--untracked-files=all','--','.'])==='',
    source_sha256:sourceFingerprint(process.cwd()),allowed_source_files:[seam],
    seam_sha256:sha(readFileSync(seam)),protected_files_sha256:sha(JSON.stringify(protectedFiles)),
    export_files,regression_commands:commands});
}
try {
  const current=snapshot();
  if (process.argv.length===2) {
    process.stdout.write(JSON.stringify(current,null,2)+'\n');
  } else if (process.argv.length===4 && process.argv[2]==='--verify') {
    const baseline=Contract.parse(JSON.parse(readFileSync(resolve(process.argv[3]),'utf8')));
    if (!baseline.source_clean || !current.source_clean) throw new Error('repair_source_not_clean');
    if (JSON.stringify(baseline.allowed_source_files)!==JSON.stringify([seam]) || JSON.stringify(baseline.regression_commands)!==JSON.stringify(commands)) throw new Error('invalid_baseline_contract');
    if (sha(JSON.stringify(baseline.export_files.filter(file=>file.path!==seam)))!==baseline.protected_files_sha256) throw new Error('invalid_baseline_digest');
    if (baseline.protected_files_sha256!==current.protected_files_sha256) throw new Error('protected_source_changed');
    if (baseline.seam_sha256===current.seam_sha256 || baseline.source_commit===current.source_commit) throw new Error('repair_source_unchanged');
    process.stdout.write(JSON.stringify({valid:true,source_commit:current.source_commit,source_sha256:current.source_sha256,seam_sha256:current.seam_sha256,protected_files_sha256:current.protected_files_sha256})+'\n');
  } else {
    throw new Error('usage: repair:contract [--verify <private-original-contract.json>]');
  }
} catch (error) {
  process.stderr.write(JSON.stringify({error:error instanceof Error ? error.message:'repair_contract_error'})+'\n');
  process.exitCode=1;
}
