#!/bin/bash
set -euo pipefail
cd /opt/pandora34-server
mkdir -p /tmp/cfs-bak packages/api/src/routes
cp -a packages/api/src/server.ts packages/api/src/env.ts docker-compose.yml nginx/templates/default.conf.template /tmp/cfs-bak/ 2>/dev/null || true
sed -i 's/\r$//' packages/api/src/server.ts packages/api/src/env.ts docker-compose.yml nginx/templates/default.conf.template

echo "пишу firmware.ts"
cat > packages/api/src/routes/firmware.ts << 'END'
import { createHash } from "crypto";
import { promises as fs } from "fs";
import path from "path";
import { FastifyInstance } from "fastify";
import { requireDeviceToken } from "../auth/middleware";
import { env } from "../env";

const BOOT_BASE = 0x08000000;
const APP_BASE = 0x08002000;
const BOOT_MAX = APP_BASE - BOOT_BASE;

type BootMeta = {
  present: true;
  size: number;
  uploadedAt: string;
  sha256: string;
  sp: string;
  pc: string;
  note: string;
  version: string;
};

function bootPath(): string {
  return path.join(env.firmwareDir, "boot.bin");
}

function metaPath(): string {
  return path.join(env.firmwareDir, "boot.json");
}

const APP_MAX = 8 * 1024 * 1024;
const APP_ALLOW = new Set([
  "main.py",
  "app_version.py",
  "cfs_server.py",
  "uart_boot.py",
  "j2534_passthru.py",
  "j2534_worker.py",
  "control.py",
  "engine.py",
  "app_update.py",
  "help_pins.jpg",
  "requirements.txt",
  "run.bat",
]);
const APP_NEED = ["main.py", "app_version.py", "app_update.py", "j2534_passthru.py", "j2534_worker.py"];

function appDir(): string {
  return path.join(env.firmwareDir, "appfiles");
}

function appMetaPath(): string {
  return path.join(env.firmwareDir, "app.json");
}

const IMAGE_OFF = 0x2000;
const IMAGE_MAX = 0xE800;

function imagePath(): string {
  return path.join(env.firmwareDir, "firmware.bin");
}

function imageMetaPath(): string {
  return path.join(env.firmwareDir, "image.json");
}

type ImageMeta = {
  present: true;
  version: string;
  size: number;
  uploadedAt: string;
  sha256: string;
  sp: string;
  pc: string;
};

function imageVersion(buf: Buffer): string {
  const at = buf.indexOf("CFVW");
  if (at < 0) return "";
  let end = at + 4;
  const stop = Math.min(buf.length, at + 24);
  while (end < stop && buf[end] !== 0 && buf[end] !== 10) end++;
  return buf.subarray(at + 4, end).toString("ascii").replace(/[^0-9.A-Za-z]/g, "");
}

function inspectImage(buf: Buffer): { sp: number; pc: number; version: string } | null {
  if (buf.length <= IMAGE_OFF + 8 || buf.length > IMAGE_MAX) return null;
  const sp = buf.readUInt32LE(0);
  const pc = buf.readUInt32LE(4);
  if (sp < 0x20000000 || sp > 0x20010000 || (pc & 1) === 0) return null;
  const bootAddr = pc & ~1;
  if (bootAddr < BOOT_BASE || bootAddr >= BOOT_BASE + IMAGE_OFF) return null;
  const asp = buf.readUInt32LE(IMAGE_OFF);
  const apc = buf.readUInt32LE(IMAGE_OFF + 4);
  if (asp < 0x20000000 || asp > 0x20010000 || (apc & 1) === 0) return null;
  const appAddr = apc & ~1;
  if (appAddr < BOOT_BASE + IMAGE_OFF || appAddr >= BOOT_BASE + IMAGE_MAX) return null;
  return { sp, pc: bootAddr, version: imageVersion(buf) };
}

async function readImageMeta(): Promise<ImageMeta | null> {
  try {
    const raw = await fs.readFile(imageMetaPath(), "utf8");
    const parsed = JSON.parse(raw) as ImageMeta;
    if (!parsed || parsed.present !== true) return null;
    return parsed;
  } catch {
    return null;
  }
}

function inspectBoot(buf: Buffer): { sp: number; pc: number } | null {
  if (buf.length < 8 || buf.length > BOOT_MAX) return null;
  const sp = buf.readUInt32LE(0);
  const pc = buf.readUInt32LE(4);
  if (sp < 0x20000000 || sp > 0x20010000 || (pc & 1) === 0) return null;
  const addr = pc & ~1;
  if (addr < BOOT_BASE || addr >= APP_BASE) return null;
  return { sp, pc: addr };
}

function hexAddr(n: number): string {
  return "0x" + n.toString(16).toUpperCase().padStart(8, "0");
}

function bootVersion(buf: Buffer): string {
  const at = buf.indexOf("CFBW");
  if (at < 0) return "";
  let end = at + 4;
  const stop = Math.min(buf.length, at + 20);
  while (end < stop && buf[end] !== 0 && buf[end] !== 10) end++;
  return buf.subarray(at + 4, end).toString("ascii").replace(/[^0-9.]/g, "");
}

function versionOf(text: string): string {
  const m = text.match(/APP_VERSION\s*=\s*"(\d+(?:\.\d+)*)"/);
  return m ? m[1] : "";
}

type AppFileInfo = { size: number; sha256: string };

type AppMeta = {
  present: true;
  version: string;
  uploadedAt: string;
  files: Record<string, AppFileInfo>;
};

async function readAppMeta(): Promise<AppMeta | null> {
  try {
    const raw = await fs.readFile(appMetaPath(), "utf8");
    const parsed = JSON.parse(raw) as AppMeta;
    if (!parsed || parsed.present !== true || !parsed.version) return null;
    return parsed;
  } catch {
    return null;
  }
}

async function readMeta(): Promise<BootMeta | null> {
  try {
    const raw = await fs.readFile(metaPath(), "utf8");
    const parsed = JSON.parse(raw) as BootMeta;
    if (!parsed || parsed.present !== true) return null;
    return parsed;
  } catch {
    return null;
  }
}

export async function firmwareRoutes(app: FastifyInstance): Promise<void> {
  app.get("/firmware/boot/info", { preHandler: requireDeviceToken }, async () => {
    const meta = await readMeta();
    if (!meta) return { present: false };
    try {
      const buf = await fs.readFile(bootPath());
      return { ...meta, version: bootVersion(buf) };
    } catch {
      return { present: false };
    }
  });

  app.get("/firmware/boot", { preHandler: requireDeviceToken }, async (_request, reply) => {
    const meta = await readMeta();
    if (!meta) return reply.code(404).send({ error: "no_boot" });
    try {
      const buf = await fs.readFile(bootPath());
      reply.header("Cache-Control", "no-store");
      reply.header("Content-Disposition", 'attachment; filename="boot.bin"');
      return reply.type("application/octet-stream").send(buf);
    } catch {
      return reply.code(404).send({ error: "no_boot" });
    }
  });

  app.post("/firmware/boot", { preHandler: requireDeviceToken }, async (request, reply) => {
    const body = request.body;
    if (!Buffer.isBuffer(body)) return reply.code(400).send({ error: "bad_body" });
    const vec = inspectBoot(body);
    if (!vec) return reply.code(400).send({ error: "not_boot" });
    const noteRaw = request.headers["x-boot-note"];
    const note = (Array.isArray(noteRaw) ? noteRaw[0] : noteRaw || "")
      .toString()
      .trim()
      .slice(0, 200);
    const meta: BootMeta = {
      present: true,
      size: body.length,
      uploadedAt: new Date().toISOString(),
      sha256: createHash("sha256").update(body).digest("hex"),
      sp: hexAddr(vec.sp),
      pc: hexAddr(vec.pc),
      note,
      version: bootVersion(body),
    };
    await fs.mkdir(env.firmwareDir, { recursive: true });
    const tmp = bootPath() + ".tmp";
    await fs.writeFile(tmp, body);
    await fs.rename(tmp, bootPath());
    await fs.writeFile(metaPath(), JSON.stringify(meta));
    return meta;
  });

  app.get("/firmware/app/info", { preHandler: requireDeviceToken }, async () => {
    const meta = await readAppMeta();
    if (!meta) return { present: false };
    try {
      await fs.access(appDir());
    } catch {
      return { present: false };
    }
    return meta;
  });

  app.get("/firmware/app/file/:name", { preHandler: requireDeviceToken }, async (request, reply) => {
    const name = (request.params as { name?: string }).name || "";
    if (!APP_ALLOW.has(name)) return reply.code(404).send({ error: "no_file" });
    const meta = await readAppMeta();
    if (!meta || !meta.files[name]) return reply.code(404).send({ error: "no_file" });
    try {
      const buf = await fs.readFile(path.join(appDir(), name));
      reply.header("Cache-Control", "no-store");
      return reply.type("application/octet-stream").send(buf);
    } catch {
      return reply.code(404).send({ error: "no_file" });
    }
  });

  app.post("/firmware/app", { preHandler: requireDeviceToken }, async (request, reply) => {
    const body = request.body as { files?: Record<string, string> };
    const incoming = body && body.files;
    if (!incoming || typeof incoming !== "object") return reply.code(400).send({ error: "bad_body" });
    const names = Object.keys(incoming);
    if (names.some((n) => !APP_ALLOW.has(n) || n.includes("/") || n.includes(".."))) {
      return reply.code(400).send({ error: "bad_files" });
    }
    if (APP_NEED.some((n) => !names.includes(n))) return reply.code(400).send({ error: "bad_files" });
    const decoded = new Map<string, Buffer>();
    let total = 0;
    for (const name of names) {
      const raw = incoming[name];
      if (typeof raw !== "string") return reply.code(400).send({ error: "bad_body" });
      const buf = Buffer.from(raw, "base64");
      total += buf.length;
      if (total > APP_MAX) return reply.code(400).send({ error: "bad_body" });
      decoded.set(name, buf);
    }
    const version = versionOf(decoded.get("app_version.py")?.toString("utf8") || "");
    if (!version) return reply.code(400).send({ error: "no_version" });
    const files: Record<string, AppFileInfo> = {};
    const tmp = appDir() + ".tmp";
    await fs.rm(tmp, { recursive: true, force: true });
    await fs.mkdir(tmp, { recursive: true });
    for (const [name, buf] of decoded) {
      await fs.writeFile(path.join(tmp, name), buf);
      files[name] = { size: buf.length, sha256: createHash("sha256").update(buf).digest("hex") };
    }
    await fs.rm(appDir(), { recursive: true, force: true });
    await fs.rename(tmp, appDir());
    const meta: AppMeta = { present: true, version, uploadedAt: new Date().toISOString(), files };
    await fs.mkdir(env.firmwareDir, { recursive: true });
    await fs.writeFile(appMetaPath(), JSON.stringify(meta));
    return meta;
  });

  app.get("/firmware/image/info", { preHandler: requireDeviceToken }, async () => {
    const meta = await readImageMeta();
    if (!meta) return { present: false };
    try {
      await fs.access(imagePath());
    } catch {
      return { present: false };
    }
    return meta;
  });

  app.get("/firmware/image", { preHandler: requireDeviceToken }, async (_request, reply) => {
    const meta = await readImageMeta();
    if (!meta) return reply.code(404).send({ error: "no_image" });
    try {
      const buf = await fs.readFile(imagePath());
      reply.header("Cache-Control", "no-store");
      reply.header("Content-Disposition", 'attachment; filename="firmware.bin"');
      return reply.type("application/octet-stream").send(buf);
    } catch {
      return reply.code(404).send({ error: "no_image" });
    }
  });

  app.post("/firmware/image", { preHandler: requireDeviceToken }, async (request, reply) => {
    const body = request.body;
    if (!Buffer.isBuffer(body)) return reply.code(400).send({ error: "bad_body" });
    const img = inspectImage(body);
    if (!img) return reply.code(400).send({ error: "not_image" });
    const meta: ImageMeta = {
      present: true,
      version: img.version,
      size: body.length,
      uploadedAt: new Date().toISOString(),
      sha256: createHash("sha256").update(body).digest("hex"),
      sp: hexAddr(img.sp),
      pc: hexAddr(img.pc),
    };
    await fs.mkdir(env.firmwareDir, { recursive: true });
    const tmp = imagePath() + ".tmp";
    await fs.writeFile(tmp, body);
    await fs.rename(tmp, imagePath());
    await fs.writeFile(imageMetaPath(), JSON.stringify(meta));
    return meta;
  });
}
END

echo "правлю server.ts"
S=packages/api/src/server.ts
grep -q 'import { syncRoutes } from "./routes/sync";' "$S" || { echo "server.ts: нет строки syncRoutes"; exit 1; }
grep -q 'await app.register(cors, { origin: true });' "$S" || { echo "server.ts: нет cors"; exit 1; }
grep -q 'await app.register(syncRoutes);' "$S" || { echo "server.ts: нет register sync"; exit 1; }
if ! grep -q 'routes/firmware' "$S"; then
  awk '{
    print
    if ($0 ~ /import \{ syncRoutes \} from "\.\/routes\/sync"/)
      print "import { firmwareRoutes } from \"./routes/firmware\";"
  }' "$S" > /tmp/cfs-server.ts
  mv /tmp/cfs-server.ts "$S"
fi
if ! grep -q 'application/octet-stream' "$S"; then
  awk '{
    print
    if ($0 ~ /await app.register\(cors, \{ origin: true \}\)/) {
      print ""
      print "  app.addContentTypeParser(\"application/octet-stream\", { parseAs: \"buffer\" }, (_req, body, done) => {"
      print "    done(null, body);"
      print "  });"
    }
  }' "$S" > /tmp/cfs-server.ts
  mv /tmp/cfs-server.ts "$S"
fi
if ! grep -q 'register(firmwareRoutes)' "$S"; then
  awk '{
    print
    if ($0 ~ /await app.register\(syncRoutes\)/)
      print "  await app.register(firmwareRoutes);"
  }' "$S" > /tmp/cfs-server.ts
  mv /tmp/cfs-server.ts "$S"
fi
grep -q 'register(firmwareRoutes)' "$S"
grep -q 'application/octet-stream' "$S"

echo "правлю env.ts"
E=packages/api/src/env.ts
if ! grep -q firmwareDir "$E"; then
  awk '{ line[NR]=$0 }
  END {
    last=0
    for (i=1;i<=NR;i++) if (line[i] ~ /^[[:space:]]*}[;]?[[:space:]]*$/) last=i
    if (!last) exit 2
    j=last-1
    while (j>0 && line[j] ~ /^[[:space:]]*$/) j--
    if (j>0 && line[j] !~ /,$/) line[j]=line[j] ","
    for (i=1;i<=NR;i++) {
      if (i==last) print "  firmwareDir: (process.env.FIRMWARE_DIR ?? \"\").trim() || \"firmware\","
      print line[i]
    }
  }' "$E" > /tmp/cfs-env.ts || { echo "env.ts: не нашёл закрытие объекта"; echo "---- хвост env.ts ----"; tail -n 20 "$E"; exit 1; }
  mv /tmp/cfs-env.ts "$E"
fi
grep -q firmwareDir "$E"

echo "правлю docker-compose.yml"
C=docker-compose.yml
grep -q 'REDIS_URL: redis://redis:6379' "$C" || { echo "compose: нет REDIS_URL"; exit 1; }
if ! grep -q 'FIRMWARE_DIR' "$C"; then
  awk '{
    print
    if ($0 ~ /REDIS_URL: redis:\/\/redis:6379/)
      print "      FIRMWARE_DIR: /data/firmware"
  }' "$C" > /tmp/cfs-compose.yml
  mv /tmp/cfs-compose.yml "$C"
fi
if ! grep -q 'firmwaredata:/data/firmware' "$C"; then
  awk '
    BEGIN { done=0; seen=0 }
    {
      if ($0 ~ /REDIS_URL: redis:\/\/redis:6379/) seen=1
      if (seen && !done && $0 ~ /^    expose:/) {
        print "    volumes:"
        print "      - firmwaredata:/data/firmware"
        done=1
      }
      print
    }
    END { if (!done) exit 1 }
  ' "$C" > /tmp/cfs-compose.yml
  mv /tmp/cfs-compose.yml "$C"
fi
if ! grep -q '^  firmwaredata:' "$C"; then
  grep -q '^  redisdata:' "$C" || { echo "compose: нет тома redisdata"; exit 1; }
  awk '{
    print
    if ($0 ~ /^  redisdata:$/) print "  firmwaredata:"
  }' "$C" > /tmp/cfs-compose.yml
  mv /tmp/cfs-compose.yml "$C"
fi
grep -q 'FIRMWARE_DIR' "$C"
grep -q 'firmwaredata:/data/firmware' "$C"
grep -q '^  firmwaredata:' "$C"

echo "правлю nginx"
N=nginx/templates/default.conf.template
NGINX_CHANGED=0
if ! grep -q 'firmware' "$N"; then
  if grep -q 'health|auth|sync' "$N"; then
    awk '{ gsub(/health\|auth\|sync/, "health|auth|sync|firmware"); print }' "$N" > /tmp/cfs-nginx.template
    mv /tmp/cfs-nginx.template "$N"
    NGINX_CHANGED=1
  else
    echo "nginx: нет строки health|auth|sync"
    exit 1
  fi
fi
grep -q 'firmware' "$N"

echo "собираю API, подожди"
docker compose up -d --build api
if [ "$NGINX_CHANGED" = 1 ]; then
  echo "перезапускаю nginx"
  docker compose up -d --force-recreate nginx
fi
docker compose ps api nginx
echo "готово"
