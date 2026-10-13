import { Inflate } from "fflate";

export const PACKAGE_BYTES = 4 * 1024 * 1024;
export const PACKAGE_MEMBERS = 160;
export const utf8Decode = (bytes: Uint8Array): string => new TextDecoder("utf-8", {fatal: true, ignoreBOM: true}).decode(bytes);
export const scalarText = (value: unknown, max: number, nonblank = false): value is string =>
  typeof value === "string" && value.length > 0 && [...value].length <= max &&
  !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value) && (!nonblank || !!value.trim());

/** Parse before constructing objects so repeated, escaped JSON keys cannot overwrite evidence. */
export function strictJsonParse(source: string): unknown {
  let i = source.charCodeAt(0) === 0xfeff ? 1 : 0;
  const fail = (): never => {throw new Error("The package JSON is invalid or contains duplicate keys.");};
  const space = () => {while (/[\t\r\n ]/.test(source[i] || "x")) i++;};
  const string = (): string => {
    const start = i++;
    while (i < source.length) {
      const char = source[i++];
      if (char === '"') {
        let result: string;
        try {result = JSON.parse(source.slice(start, i));} catch {return fail();}
        if (/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(result)) return fail();
        return result;
      }
      if (char === "\\") i++;
    }
    return fail();
  };
  const value = (depth: number): unknown => {
    if (depth > 64) return fail();
    space();
    const char = source[i];
    if (char === '"') return string();
    if (char === "{") {
      i++; space();
      const result: Record<string, unknown> = {};
      const seen = new Set<string>();
      if (source[i] === "}") {i++; return result;}
      while (i < source.length) {
        space(); if (source[i] !== '"') return fail();
        const key = string(); if (seen.has(key)) return fail(); seen.add(key);
        space(); if (source[i++] !== ":") return fail();
        Object.defineProperty(result, key, {value: value(depth + 1), enumerable: true, configurable: true, writable: true});
        space(); const next = source[i++]; if (next === "}") return result; if (next !== ",") return fail();
      }
      return fail();
    }
    if (char === "[") {
      i++; space(); const result: unknown[] = [];
      if (source[i] === "]") {i++; return result;}
      while (i < source.length) {
        result.push(value(depth + 1)); space(); const next = source[i++];
        if (next === "]") return result; if (next !== ",") return fail();
      }
      return fail();
    }
    for (const [word, result] of [["true", true], ["false", false], ["null", null]] as const) {
      if (source.startsWith(word, i)) {i += word.length; return result;}
    }
    const token = source.slice(i).match(/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/);
    if (!token) return fail();
    i += token[0].length; const number = Number(token[0]); if (!Number.isFinite(number)) return fail();
    return number;
  };
  const result = value(0); space(); if (i !== source.length) return fail(); return result;
}

const crcTable = Uint32Array.from({length: 256}, (_, n) => {
  let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0;
});
function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff; for (const byte of bytes) crc = crcTable[(crc ^ byte) & 255] ^ (crc >>> 8); return (crc ^ 0xffffffff) >>> 0;
}
interface ZipMember {name: string; size: number; compressed: number; crc: number; flags: number; method: number; offset: number; start: number; end: number}

/** Inspect both ZIP directories before inflation; each expansion is bounded and its CRC checked. */
export function unzipPackage(bytes: Uint8Array): Record<string, Uint8Array> {
  const fail = (detail = "structure"): never => {throw new Error(`The ZIP package failed its ${detail} validation.`);};
  if (!bytes.length || bytes.length > PACKAGE_BYTES) return fail("4 MiB size");
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const u16 = (at: number) => {if (at < 0 || at + 2 > bytes.length) return fail(); return view.getUint16(at, true);};
  const u32 = (at: number) => {if (at < 0 || at + 4 > bytes.length) return fail(); return view.getUint32(at, true);};
  let end = -1;
  for (let at = bytes.length - 22; at >= Math.max(0, bytes.length - 65557); at--) {
    if (u32(at) === 0x06054b50 && at + 22 + u16(at + 20) === bytes.length) {end = at; break;}
  }
  if (end < 0 || u16(end + 4) || u16(end + 6)) return fail();
  const count = u16(end + 10), directorySize = u32(end + 12), directory = u32(end + 16);
  if (!count || count > PACKAGE_MEMBERS || u16(end + 8) !== count || directory + directorySize !== end) return fail("member count or directory");
  const members: ZipMember[] = [], names = new Set<string>(); let cursor = directory, expanded = 0;
  for (let n = 0; n < count; n++) {
    if (u32(cursor) !== 0x02014b50 || cursor + 46 > end) return fail();
    const flags = u16(cursor + 8), method = u16(cursor + 10), compressed = u32(cursor + 20), size = u32(cursor + 24);
    const nameSize = u16(cursor + 28), extra = u16(cursor + 30), comment = u16(cursor + 32), offset = u32(cursor + 42);
    if (flags & ~0x808 || ![0, 8].includes(method) || u16(cursor + 34) || cursor + 46 + nameSize + extra + comment > end) return fail();
    const name = utf8Decode(bytes.subarray(cursor + 46, cursor + 46 + nameSize));
    if (!name || name.includes("\\") || name.includes("\0") || name.includes(":") || name.startsWith("/") || name.split("/").some(p => !p || p === "." || p === "..")) return fail("path");
    if (names.has(name)) return fail("duplicate path"); names.add(name);
    expanded += size; if (size > PACKAGE_BYTES || expanded > PACKAGE_BYTES) return fail("expanded size");
    members.push({name, size, compressed, crc: u32(cursor + 16), flags, method, offset, start: 0, end: 0});
    cursor += 46 + nameSize + extra + comment;
  }
  if (cursor !== end) return fail();
  members.sort((a, b) => a.offset - b.offset); let next = 0;
  for (const member of members) {
    const at = member.offset;
    if (at !== next || u32(at) !== 0x04034b50 || u16(at + 6) !== member.flags || u16(at + 8) !== member.method) return fail("local header");
    const nameSize = u16(at + 26), extra = u16(at + 28);
    if (utf8Decode(bytes.subarray(at + 30, at + 30 + nameSize)) !== member.name) return fail("local path");
    member.start = at + 30 + nameSize + extra; member.end = member.start + member.compressed;
    if (member.end > directory) return fail();
    if (!(member.flags & 8) && (u32(at + 14) !== member.crc || u32(at + 18) !== member.compressed || u32(at + 22) !== member.size)) return fail("local size");
    next = member.end;
    if (member.flags & 8) {
      if (u32(next) === 0x08074b50) next += 4;
      if (u32(next) !== member.crc || u32(next + 4) !== member.compressed || u32(next + 8) !== member.size) return fail("descriptor");
      next += 12;
    }
  }
  if (next !== directory) return fail("local directory");
  const result: Record<string, Uint8Array> = Object.create(null);
  for (const member of members) {
    const compressed = bytes.subarray(member.start, member.end); let output: Uint8Array;
    if (member.method === 0) output = new Uint8Array(compressed);
    else {
      const chunks: Uint8Array[] = []; let length = 0;
      const inflater = new Inflate((chunk) => {
        length += chunk.length; if (length > member.size || length > PACKAGE_BYTES) return fail("expanded size");
        chunks.push(new Uint8Array(chunk));
      });
      // A small input slice also bounds temporary output from a deliberately high-ratio stream.
      for (let i = 0; i < compressed.length; i += 256) inflater.push(compressed.subarray(i, i + 256), i + 256 >= compressed.length);
      if (!compressed.length) inflater.push(compressed, true);
      output = new Uint8Array(length); let at = 0; for (const chunk of chunks) {output.set(chunk, at); at += chunk.length;}
    }
    if (output.length !== member.size || crc32(output) !== member.crc) return fail("size or CRC");
    result[member.name] = output;
  }
  return result;
}
