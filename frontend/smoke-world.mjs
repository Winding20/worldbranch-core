// 第二个世界的渲染冒烟：直接打开 ?world=sanguo_208。
//
// 它要证明两件事：
//   1. 前端能承载任意世界包（标题、角色、期限都来自 pack）；
//   2. 没有边界数据的世界不会白屏——退回用经纬度画点。
//
// 用法：node smoke-world.mjs [后端地址] [世界key]

import { JSDOM } from "jsdom";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BASE = process.argv[2] || "http://127.0.0.1:5210";
const WORLD = process.argv[3] || "sanguo_208";
const BUNDLE = path.join(HERE, "dist-smoke", "app.js");
const CSS = fs
  .readdirSync(path.join(HERE, "dist", "assets"))
  .filter((f) => f.endsWith(".css"))
  .map((f) => path.join(HERE, "dist", "assets", f))[0];

if (!fs.existsSync(BUNDLE)) {
  console.error("缺少 IIFE 包，请先执行 node build.mjs");
  process.exit(2);
}

const dom = new JSDOM(
  `<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><div id="app"></div></body></html>`,
  {
    url: `${BASE}/app/?world=${WORLD}`,
    runScripts: "outside-only",
    pretendToBeVisual: true,
  }
);
const { window } = dom;

window.fetch = (input, init) => {
  const url = typeof input === "string" && input.startsWith("http") ? input : BASE + input;
  return fetch(url, init);
};
window.matchMedia =
  window.matchMedia || (() => ({ matches: false, addListener() {}, removeListener() {} }));

if (CSS) {
  const style = window.document.createElement("style");
  style.textContent = fs.readFileSync(CSS, "utf8");
  window.document.head.appendChild(style);
}

const errors = [];
window.addEventListener("error", (e) => errors.push(e.message));
window.addEventListener("unhandledrejection", (e) => errors.push(String(e.reason)));

window.eval(fs.readFileSync(BUNDLE, "utf8"));

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const deadline = Date.now() + 25000;
let body = "";
while (Date.now() < deadline) {
  await wait(400);
  body = window.document.getElementById("app").innerHTML;
  if (body.includes(WORLD) || body.length > 4000) break;
}
await wait(900);
body = window.document.getElementById("app").innerHTML;

let failed = 0;
function check(name, ok) {
  console.log(`${ok ? "✓" : "✗"} ${name}`);
  if (!ok) failed += 1;
}

console.log(`\n【世界 ${WORLD}】`);
check("页面已挂载", body.length > 200);
check("显示出该世界的标题", /赤壁/.test(body));
check("HUD 显示角色", /刘备/.test(body));
check("没有边界时用圆点渲染", window.document.querySelectorAll(".region-dots circle").length > 0);
check("地区名出现在画面里", /江夏|江陵|柴桑|襄阳/.test(body));

console.log("\n【无运行时错误】");
check("无运行时错误", errors.length === 0);
if (errors.length) {
  errors.slice(0, 5).forEach((e) => console.log("  -", e));
}

console.log(failed === 0 ? "\n✓ 第二个世界渲染通过" : `\n✗ ${failed} 项失败`);
process.exit(failed === 0 ? 0 : 1);