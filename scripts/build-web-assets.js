const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const copies = {
  "node_modules/marked/lib/marked.umd.js": "web/vendor/marked.umd.js",
  "node_modules/dompurify/dist/purify.min.js": "web/vendor/purify.min.js",
  "node_modules/@fontsource-variable/plus-jakarta-sans/files/plus-jakarta-sans-latin-wght-normal.woff2": "web/fonts/plus-jakarta-sans-latin.woff2",
  "node_modules/@fontsource-variable/inter/files/inter-latin-wght-normal.woff2": "web/fonts/inter-latin.woff2",
  "node_modules/@fontsource-variable/jetbrains-mono/files/jetbrains-mono-latin-wght-normal.woff2": "web/fonts/jetbrains-mono-latin.woff2",
  "node_modules/@fontsource-variable/cinzel/files/cinzel-latin-wght-normal.woff2": "web/fonts/cinzel-latin.woff2",
  "node_modules/marked/LICENSE.md": "web/licenses/marked-LICENSE.md",
  "node_modules/dompurify/LICENSE": "web/licenses/dompurify-LICENSE.txt",
  "node_modules/@fontsource-variable/plus-jakarta-sans/LICENSE": "web/licenses/plus-jakarta-sans-OFL.txt",
  "node_modules/@fontsource-variable/inter/LICENSE": "web/licenses/inter-OFL.txt",
  "node_modules/@fontsource-variable/jetbrains-mono/LICENSE": "web/licenses/jetbrains-mono-OFL.txt",
  "node_modules/@fontsource-variable/cinzel/LICENSE": "web/licenses/cinzel-OFL.txt",
};

for (const [source, destination] of Object.entries(copies)) {
  const sourcePath = path.join(root, source);
  const destinationPath = path.join(root, destination);
  if (!fs.existsSync(sourcePath)) throw new Error(`Dependência ausente: ${source}`);
  fs.mkdirSync(path.dirname(destinationPath), { recursive: true });
  fs.copyFileSync(sourcePath, destinationPath);
}
