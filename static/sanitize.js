/* Escape untrusted flight-log text before it is placed in HTML or a spreadsheet. */
"use strict";

function esc(v) {
  return String(v ?? "\u2014").replace(/\u0000/g, "").replace(/[&<>"']/g, c => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

function csvField(v) {
  if (v == null) return "";
  let s = String(v).replace(/\u0000/g, "");
  // Plain numbers stay numeric. Other cells that start with a formula
  // trigger are text, so a log string cannot become a spreadsheet formula.
  const plainNumber = /^-?\d+(\.\d+)?$/.test(s);
  const formula = /^[=+\-@\t\r]/.test(s) && !plainNumber;
  if (formula) s = "'" + s;
  if (formula || /[",\n\r]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
  return s;
}
