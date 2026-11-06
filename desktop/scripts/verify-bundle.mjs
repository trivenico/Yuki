import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (relative) => readFile(path.join(root, relative), "utf8");

const [html, css, js] = await Promise.all([
  read("dist/index.html"),
  read("dist/assets/chat.css"),
  read("dist/assets/chat.js"),
]);

const requiredHtml = [
  'href="assets/chat.css"',
  'src="assets/chat.js"',
  'id="notification-preferences-title"',
  'id="enable-notifications"',
  'id="disable-notifications"',
  'id="refresh-notification-status"',
  'id="notification-worker-status"',
];
const requiredCss = [
  ".notification-preferences",
  ".notification-actions",
  "#notification-worker-status",
];
const requiredJs = [
  'invoke("notification_worker_status")',
  'invoke("request_notification_permission")',
  'invoke("start_notification_worker")',
  'invoke("stop_notification_worker")',
  'addEventListener("click", refreshNotificationWorkerStatus)',
  'addEventListener("visibilitychange"',
];

const missing = [
  ...requiredHtml.filter((value) => !html.includes(value)).map((value) => `HTML: ${value}`),
  ...requiredCss.filter((value) => !css.includes(value)).map((value) => `CSS: ${value}`),
  ...requiredJs.filter((value) => !js.includes(value)).map((value) => `JS: ${value}`),
];

if (missing.length) {
  process.stderr.write("Desktop bundle verification failed. Missing required native UI integration:\\n");
  for (const item of missing) process.stderr.write(`- ${item}\\n`);
  process.exitCode = 1;
} else {
  process.stdout.write("Desktop bundle verified: local assets and native notification controls/handlers are present.\\n");
}
