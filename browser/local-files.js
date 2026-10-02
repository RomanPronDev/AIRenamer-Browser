/* Session-only browser grants. Keep FileEntry snapshots intact: wrapping their
 * File in new File(...) discards Chromium's native backing-file information. */
((scope) => {
  "use strict";
  const normalize = path => String(path || "").replace(/\\/g, "/").replace(/\/+$/, "");
  function relativePath(root, path) {
    const base = normalize(root), full = normalize(path);
    if (!base || !full.toLowerCase().startsWith(base.toLowerCase() + "/"))
      throw new Error("File is outside the connected project folder.");
    const parts = full.slice(base.length + 1).split("/");
    if (parts.some(part => !part || part === "." || part === ".." || part.includes(":")))
      throw new Error("Invalid relative file path.");
    return parts.join("/");
  }
  class LocalFiles {
    constructor() { this.projects = new Map(); this.cache = new Map(); }
    connected(project) { return !!project && this.projects.get(project.name)?.root === normalize(project.path); }
    connect(project, entry) {
      if (!entry?.isDirectory) throw new Error("Drop the project folder from Explorer here.");
      if (entry.name.toLowerCase() !== normalize(project.path).split("/").pop().toLowerCase())
        throw new Error("Choose the project root folder named " + normalize(project.path).split("/").pop() + ".");
      this.disconnect(project.name);
      this.projects.set(project.name, {root: normalize(project.path), entry});
    }
    disconnect(name) {
      this.projects.delete(name);
      for (const [key, value] of this.cache) if (value.project === name) this.cache.delete(key);
    }
    key(project, item) { return project.name + "\0" + project.path + "\0" + item.path + "\0" + item.size + "\0" + item.modified; }
    get(project, item) { return this.cache.get(this.key(project, item))?.file || null; }
    prepare(project, item) {
      if (!this.connected(project) || !item?.path || item.converted) return Promise.resolve(null);
      const key = this.key(project, item), cached = this.cache.get(key);
      if (cached) return cached.pending || Promise.resolve(cached.file);
      const grant = this.projects.get(project.name);
      let relative;
      try { relative = relativePath(grant.root, item.path); } catch (error) { return Promise.reject(error); }
      const record = {project: project.name, file: null};
      const pending = new Promise((resolve, reject) => {
        // Exact lookup only. Never recursively enumerate a project or read bytes.
        grant.entry.getFile(relative, {create: false}, entry => entry.file(resolve, reject), reject);
      }).then(file => {
        if (file.name !== item.name || (Number.isFinite(item.size) && file.size !== item.size) ||
            (Number.isFinite(item.modified) && Math.abs(file.lastModified - item.modified * 1000) > 2000))
          throw new Error("The connected folder contains a different file. Reconnect the correct project folder.");
        if (this.projects.get(project.name) !== grant) return null;
        record.file = file; record.pending = null;
        return file;
      }).catch(error => { if (this.cache.get(key) === record) this.cache.delete(key); throw error; });
      record.pending = pending;
      this.cache.set(key, record);
      if (this.cache.size > 64) this.cache.delete(this.cache.keys().next().value);
      return pending;
    }
  }
  if (typeof module !== "undefined" && module.exports) module.exports = {LocalFiles, relativePath};
  else scope.AIRenamerLocalFiles = LocalFiles;
})(globalThis);
