"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.join(__dirname, "..");
const context = {};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(root, "static", "sanitize.js"), "utf8"), context);

const { esc, csvField } = context;

const name = '<img src=x onerror="alert(1)">';
const serial = 'SN,"<>&\'=';
const city = "=1+1,\"<b>\n";
const formula = '=HYPERLINK("http://evil.example","click")';
const command = '=cmd|" /C calc"!A0';

assert.strictEqual(
  esc(name),
  "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;",
);
assert.strictEqual(esc(serial), "SN,&quot;&lt;&gt;&amp;&#39;=");
assert.strictEqual(esc("a&b'c"), "a&amp;b&#39;c");
assert.strictEqual(esc(null), "\u2014");
assert.strictEqual(esc(0), "0");
assert.strictEqual(esc("a\u0000b<c"), "ab&lt;c");

assert.strictEqual(csvField(name), '"<img src=x onerror=""alert(1)"">"');
assert.strictEqual(csvField(serial), '"SN,""<>&\'="');
assert.strictEqual(csvField(city), "\"'=1+1,\"\"<b>\n\"");
assert.strictEqual(
  csvField(formula),
  "\"'=HYPERLINK(\"\"http://evil.example\"\",\"\"click\"\")\"",
);
assert.strictEqual(csvField(command), "\"'=cmd|\"\" /C calc\"\"!A0\"");
assert.strictEqual(csvField("-3.2"), "-3.2");
assert.strictEqual(csvField(-3.2), "-3.2");
assert.strictEqual(csvField("-2+3"), "\"'-2+3\"");
assert.strictEqual(csvField("+1"), "\"'+1\"");
assert.strictEqual(csvField("@SUM(A1)"), "\"'@SUM(A1)\"");
assert.strictEqual(csvField(null), "");
assert.strictEqual(csvField(0), "0");
assert.strictEqual(csvField("a\nb"), '"a\nb"');
assert.strictEqual(csvField("a\u0000b"), "ab");

const app = fs.readFileSync(path.join(root, "static", "app.js"), "utf8");
const rowAt = app.indexOf("const row =");
const row = app.slice(rowAt, rowAt + 180);
assert.ok(row.includes("esc(k)") && row.includes("esc(v)"), row);
assert.ok(app.includes('esc(v ?? "")'));
assert.ok(app.includes("r.map(csvField)"));
assert.ok(app.includes("csvField(x[k]"));
assert.ok(!app.includes("const esc"));

console.log("sanitize tests passed");
