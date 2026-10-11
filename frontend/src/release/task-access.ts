const prefix = "cluetide.release.task-write.v1:";
const memory = new Map<string,string>();
const invalid = new Set<string>();
const validKey = (value:unknown):value is string => typeof value === "string" && /^[A-Za-z0-9_-]{32,128}$/.test(value);

export const taskAccess = {
  prepareStorage():void {
    try { const probe=prefix+"probe";localStorage.setItem(probe,"ready");localStorage.removeItem(probe); }
    catch { throw new Error("Enable local storage before creating a release so this device can retain package write access."); }
  },
  save(taskId:string,key:unknown):void {
    if(!validKey(key))throw new Error("The service did not return valid package write access.");
    memory.set(taskId,key);
    invalid.delete(taskId);
    try { localStorage.setItem(prefix+taskId,key); } catch { /* The current tab retains access. */ }
  },
  key(taskId:string):string|undefined {
    if(invalid.has(taskId))return undefined;
    const cached=memory.get(taskId);if(cached)return cached;
    try { const stored=localStorage.getItem(prefix+taskId);if(validKey(stored)){memory.set(taskId,stored);return stored;} } catch { /* Use current-tab access when available. */ }
    return undefined;
  },
  persistent(taskId:string):boolean {
    try { return localStorage.getItem(prefix+taskId)===this.key(taskId) && !!this.key(taskId); } catch { return false; }
  },
  forget(taskId:string):void {
    memory.delete(taskId);invalid.add(taskId);
    try { localStorage.removeItem(prefix+taskId); } catch { /* Access remains invalid in this tab. */ }
  },
};
