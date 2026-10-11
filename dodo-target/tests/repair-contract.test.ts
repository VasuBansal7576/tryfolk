import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { tmpdir } from 'node:os';
import { test } from 'node:test';

test('public repair contract exports no secrets or upstream and rejects changes to verification, configuration or tests',()=>{
  const original=process.cwd(),dir=mkdtempSync(join(tmpdir(),'tryfolk-dodo-seam-')),source=join(dir,'source');
  const cli=join(original,'node_modules/tsx/dist/cli.mjs');
  mkdirSync(source);
  const paths=execFileSync('git',['ls-files','-z','--','.'],{cwd:original,encoding:'utf8'}).split('\0').filter(Boolean)
    .filter(path=>!path.startsWith('upstream/') && !path.startsWith('.env'));
  for (const path of paths) {mkdirSync(dirname(join(source,path)),{recursive:true});copyFileSync(join(original,path),join(source,path));}
  symlinkSync(join(original,'node_modules'),join(source,'node_modules'),'dir');
  function git(args:string[]) {return execFileSync('git',args,{cwd:source,stdio:'ignore'});}
  function commit() {git(['add','--',...paths]);git(['-c','user.name=Tryfolk Test Fixture','-c','user.email=fixture@tryfolk.invalid','commit','-qm','local external-fixture source snapshot']);}
  function run(...args:string[]) {return spawnSync(process.execPath,[cli,'scripts/repair-contract.ts',...args],{cwd:source,encoding:'utf8'});}
  try {
    git(['init','-q']);writeFileSync(join(source,'.git/info/exclude'),'node_modules\n');commit();
    const snapshot=run();assert.equal(snapshot.status,0,snapshot.stderr);
    const baseline=JSON.parse(snapshot.stdout);
    assert.equal(baseline.source_clean,true);
    assert.deepEqual(baseline.allowed_source_files,['lib/fulfillment-policy.ts']);
    assert.ok(baseline.export_files.every((file:{path:string})=>!file.path.startsWith('upstream/') && !file.path.startsWith('.env')));
    const record=join(dir,'baseline.json');writeFileSync(record,snapshot.stdout);
    assert.match(run('--verify',record).stderr,/repair_source_unchanged/);
    writeFileSync(join(source,'lib/fulfillment-policy.ts'),"export function specialDownloadsDisposition(mode: 'healthy' | 'broken'): 'granted' | 'suppressed' { return 'granted'; }\n");
    assert.match(run('--verify',record).stderr,/repair_source_not_clean/);
    commit();const accepted=run('--verify',record);assert.equal(accepted.status,0,accepted.stderr);assert.equal(JSON.parse(accepted.stdout).valid,true);
    for (const path of ['lib/provider.ts','lib/config.ts','tests/target.test.ts']) {
      const file=join(source,path),before=readFileSync(file);
      writeFileSync(file,Buffer.concat([before,Buffer.from('\n// rejected fixture change\n')]));commit();
      const rejected=run('--verify',record);assert.notEqual(rejected.status,0);assert.match(rejected.stderr,/protected_source_changed/);
      writeFileSync(file,before);commit();
    }
  } finally {rmSync(dir,{recursive:true,force:true});}
});
