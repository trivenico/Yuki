import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const icons = path.join(root, "src-tauri", "icons");
const size = 32;
const xorBytes = size * size * 4;
const maskStride = Math.ceil(size / 32) * 4;
const maskBytes = maskStride * size;
const dibBytes = 40 + xorBytes + maskBytes;
const ico = Buffer.alloc(6 + 16 + dibBytes);

// ICONDIR + one 32x32, 32-bit image directory entry.
ico.writeUInt16LE(0, 0);
ico.writeUInt16LE(1, 2);
ico.writeUInt16LE(1, 4);
ico.writeUInt8(size, 6);
ico.writeUInt8(size, 7);
ico.writeUInt8(0, 8);
ico.writeUInt8(0, 9);
ico.writeUInt16LE(1, 10);
ico.writeUInt16LE(32, 12);
ico.writeUInt32LE(dibBytes, 14);
ico.writeUInt32LE(22, 18);

// BITMAPINFOHEADER. ICO DIB height includes XOR and AND planes.
const dib = 22;
ico.writeUInt32LE(40, dib);
ico.writeInt32LE(size, dib + 4);
ico.writeInt32LE(size * 2, dib + 8);
ico.writeUInt16LE(1, dib + 12);
ico.writeUInt16LE(32, dib + 14);
ico.writeUInt32LE(0, dib + 16);
ico.writeUInt32LE(xorBytes, dib + 20);

const pixelOffset = dib + 40;
for (let y = 0; y < size; y += 1) {
  for (let x = 0; x < size; x += 1) {
    const logicalY = size - 1 - y;
    const leftStem = Math.abs(x - (9 + logicalY * 0.42)) <= 2;
    const rightStem = Math.abs(x - (23 - logicalY * 0.42)) <= 2;
    const centerStem = logicalY >= 14 && logicalY <= 27 && Math.abs(x - 16) <= 2;
    const isMark = (logicalY <= 18 && (leftStem || rightStem)) || centerStem;
    const offset = pixelOffset + (y * size + x) * 4;
    // BGRA: deep navy background and a simple white Y glyph.
    ico[offset] = isMark ? 255 : 42;
    ico[offset + 1] = isMark ? 255 : 28;
    ico[offset + 2] = isMark ? 255 : 18;
    ico[offset + 3] = 255;
  }
}

await mkdir(icons, { recursive: true });
await writeFile(path.join(icons, "icon.ico"), ico);
process.stdout.write("Generated deterministic Yuki Windows icon.\n");
