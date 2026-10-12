import { join } from 'node:path';
import { createTarget } from './target';

declare global {
  var ownedDodoTarget: ReturnType<typeof createTarget> | undefined;
}
function runtime() {
  globalThis.ownedDodoTarget ??= createTarget({env:process.env,databasePath:process.env.TARGET_DATABASE_PATH || join(process.cwd(),'.data','target.sqlite')});
  return globalThis.ownedDodoTarget;
}
export async function dispatch(request:Request):Promise<Response> {
  try { return await runtime().handle(request); }
  catch { return Response.json({error:'database_unavailable',ready:false,errors:[{code:'database_unavailable',action:'Set TARGET_DATABASE_PATH to an owned writable persistent local SQLite file and restart.'}]},{status:503,headers:{'Cache-Control':'no-store'}}); }
}
