import { writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { sourceFingerprint } from '../lib/config';
let sourceCommit:string|null=null,sourceClean=false;
try {
  sourceCommit=execFileSync('git',['rev-parse','HEAD'],{encoding:'utf8',stdio:['ignore','pipe','ignore']}).trim();
  sourceClean=execFileSync('git',['status','--porcelain','--untracked-files=all','--','.'],{encoding:'utf8',stdio:['ignore','pipe','ignore']}).trim()==='';
} catch { /* A source archive still has its exact content fingerprint. */ }
writeFileSync('.target-manifest.json',JSON.stringify({source_sha256:sourceFingerprint(process.cwd()),source_commit:sourceCommit,source_clean:sourceClean})+'\n');
