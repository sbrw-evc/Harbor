// Checks the Russian dictionary: every literal key passed to t() / tn() in src has a translation, and translations
// keep the {placeholders} of their key. Run with `npm run check:i18n` (also part of `npm run build`).
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const SRC = fileURLToPath(new URL("../src", import.meta.url));
const files = (dir) =>
  readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) return files(p);
    return /\.(ts|tsx)$/.test(name) ? [p] : [];
  });

/** The JSON of an object literal: the same text without the commas before a closing bracket (strings kept as they are). */
function withoutTrailingCommas(s) {
  let out = "";
  let inString = false;
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (inString) {
      out += c;
      if (c === "\\") out += s[++i];
      else if (c === "\"") inString = false;
      continue;
    }
    if (c === "\"") inString = true;
    if (c === ",") {
      let j = i + 1;
      while (j < s.length && " \t\r\n".includes(s[j])) j++;
      if (s[j] === "}" || s[j] === "]") continue;
    }
    out += c;
  }
  return out;
}

/** A dictionary is `export default { "English": "Russian", … }` with whole-line comments: read as data, not run. */
function dictionary(file) {
  const code = readFileSync(file, "utf8").split("\n").filter((line) => !line.trimStart().startsWith("//")).join("\n");
  return JSON.parse(withoutTrailingCommas(code.slice(code.indexOf("{"), code.lastIndexOf("}") + 1)));
}

const ru = {};
for (const f of files(join(SRC, "i18n", "ru"))) Object.assign(ru, dictionary(f));

const str = String.raw`(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'|\x60([^\x60$]*)\x60)`;
const T = new RegExp(String.raw`\b[tk]\(\s*` + str, "g");
const TN = new RegExp(String.raw`\btn\([^,]+,\s*` + str + String.raw`\s*,\s*` + str, "g");
const unescape = (s) => s.replace(/\\(.)/g, "$1");

const missing = [];
const russian = [];
for (const f of files(SRC)) {
  if (f.includes(`${join("i18n", "ru")}`)) continue;
  const code = readFileSync(f, "utf8");
  // Text is written in English and translated; only the name of the language itself is Russian.
  code.split("\n").forEach((line, i) => {
    if (/[А-Яа-яЁё]/.test(line.replace(/"Русский"|'Русский'/g, "").split("//")[0])) russian.push(`${relative(SRC, f)}:${i + 1}`);
  });
  for (const m of code.matchAll(T)) {
    const key = unescape(m[1] ?? m[2] ?? m[3]);
    if (!(key in ru)) missing.push(`${relative(SRC, f)}: ${key}`);
  }
  // Menu entries are keys too (translated where the menu is drawn).
  if (f.endsWith("nav.ts"))
    for (const m of code.matchAll(/\b(?:label|title): "([^"]+)"/g)) if (!(m[1] in ru)) missing.push(`${relative(SRC, f)}: ${m[1]}`);
  for (const m of code.matchAll(TN)) {
    const many = unescape(m[4] ?? m[5] ?? m[6]);
    if (!(many in ru)) missing.push(`${relative(SRC, f)}: ${many} (plural)`);
  }
}

const vars = (s) => [...new Set([...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]))].sort((a, b) => a.localeCompare(b)).join();
const broken = Object.entries(ru).filter(([k, v]) => v.split("|").some((form) => vars(form) !== vars(k))).map(([k]) => k);
const cyrillicKeys = Object.keys(ru).filter((k) => /[А-Яа-яЁё]/.test(k));

if (missing.length || broken.length || cyrillicKeys.length || russian.length) {
  for (const m of [...new Set(missing)]) console.error(`missing: ${m}`);
  for (const r of russian) console.error(`Russian text in the source (use t()): ${r}`);
  for (const b of broken) console.error(`placeholders differ: ${b}`);
  for (const c of cyrillicKeys) console.error(`key is not English: ${c}`);
  process.exit(1);
}
console.log(`i18n: ${Object.keys(ru).length} Russian strings, all keys translated`);
