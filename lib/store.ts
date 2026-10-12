import { DatabaseSync } from 'node:sqlite';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import { z } from 'zod';

const Row = z.object({data:z.string()});
export class Store {
  private database: DatabaseSync;
  constructor(path: string) {
    if (path !== ':memory:') mkdirSync(dirname(path), {recursive:true, mode:0o700});
    this.database = new DatabaseSync(path);
    this.database.exec(`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;
      CREATE TABLE IF NOT EXISTS records (collection TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(collection,id));
      CREATE TABLE IF NOT EXISTS evidence (id TEXT PRIMARY KEY, data TEXT NOT NULL);
      CREATE TRIGGER IF NOT EXISTS evidence_no_update BEFORE UPDATE ON evidence BEGIN SELECT RAISE(ABORT,'immutable evidence'); END;
      CREATE TRIGGER IF NOT EXISTS evidence_no_delete BEFORE DELETE ON evidence BEGIN SELECT RAISE(ABORT,'immutable evidence'); END;`);
    this.database.exec(`CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON records WHEN OLD.collection='events' BEGIN SELECT RAISE(ABORT,'immutable event ledger'); END;
      CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON records WHEN OLD.collection='events' BEGIN SELECT RAISE(ABORT,'immutable event ledger'); END;
      CREATE TRIGGER IF NOT EXISTS fulfillments_no_update BEFORE UPDATE ON records WHEN OLD.collection='fulfillments' BEGIN SELECT RAISE(ABORT,'immutable fulfillment ledger'); END;
      CREATE TRIGGER IF NOT EXISTS fulfillments_no_delete BEFORE DELETE ON records WHEN OLD.collection='fulfillments' BEGIN SELECT RAISE(ABORT,'immutable fulfillment ledger'); END;`);
  }
  get<T>(collection:string,id:string,schema:z.ZodType<T>):T|null {
    const raw=this.database.prepare('SELECT data FROM records WHERE collection=? AND id=?').get(collection,id);
    return raw ? schema.parse(JSON.parse(Row.parse(raw).data)) : null;
  }
  list<T>(collection:string,schema:z.ZodType<T>):T[] {
    return this.database.prepare('SELECT data FROM records WHERE collection=? ORDER BY id').all(collection)
      .map(row=>schema.parse(JSON.parse(Row.parse(row).data)));
  }
  put(collection:string,id:string,data:unknown) {
    this.database.prepare('INSERT INTO records(collection,id,data) VALUES (?,?,?) ON CONFLICT(collection,id) DO UPDATE SET data=excluded.data').run(collection,id,JSON.stringify(data));
  }
  insertEvidence(id:string,data:unknown) {
    this.database.prepare('INSERT INTO evidence(id,data) VALUES (?,?)').run(id,JSON.stringify(data));
  }
  evidence(id:string):unknown|null {
    const row=this.database.prepare('SELECT data FROM evidence WHERE id=?').get(id);
    return row ? JSON.parse(Row.parse(row).data) : null;
  }
  transaction<T>(fn:()=>T):T {
    this.database.exec('BEGIN IMMEDIATE');
    try { const result=fn(); this.database.exec('COMMIT'); return result; }
    catch(error) { this.database.exec('ROLLBACK'); throw error; }
  }
  close() { this.database.close(); }
}
