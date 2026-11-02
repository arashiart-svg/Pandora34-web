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
import { inflateRawSync } from "zlib";
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

function appPath(): string {
  return path.join(env.firmwareDir, "app.zip");
}

function appMetaPath(): string {
  return path.join(env.firmwareDir, "app.json");
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

type ZipEntry = { name: string; data: Buffer };

function readZip(buf: Buffer): ZipEntry[] | null {
  const out: ZipEntry[] = [];
  let off = 0;
  while (off + 30 <= buf.length) {
    if (buf.readUInt32LE(off) !== 0x04034b50) break;
    const flags = buf.readUInt16LE(off + 6);
    const method = buf.readUInt16LE(off + 8);
    const compSize = buf.readUInt32LE(off + 18);
    const nameLen = buf.readUInt16LE(off + 26);
    const extraLen = buf.readUInt16LE(off + 28);
    const nameStart = off + 30;
    if (nameStart + nameLen + extraLen + compSize > buf.length) return null;
    const name = buf.subarray(nameStart, nameStart + nameLen).toString("utf8").replace(/\\/g, "/");
    const dataStart = nameStart + nameLen + extraLen;
    const comp = buf.subarray(dataStart, dataStart + compSize);
    if (flags & 0x1) return null;
    let data: Buffer;
    try {
      if (method === 0) data = Buffer.from(comp);
      else if (method === 8) data = inflateRawSync(comp);
      else return null;
    } catch {
      return null;
    }
    out.push({ name, data });
    off = dataStart + compSize;
  }
  return out.length ? out : null;
}

function appVersionOf(entries: ZipEntry[]): string {
  const file = entries.find((e) => e.name === "app_version.py");
  if (!file) return "";
  const text = file.data.toString("utf8");
  const m = text.match(/APP_VERSION\s*=\s*"(\d+(?:\.\d+)*)"/);
  return m ? m[1] : "";
}

type AppMeta = {
  present: true;
  version: string;
  size: number;
  uploadedAt: string;
  sha256: string;
  files: string[];
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
      await fs.access(appPath());
    } catch {
      return { present: false };
    }
    return meta;
  });

  app.get("/firmware/app", { preHandler: requireDeviceToken }, async (_request, reply) => {
    const meta = await readAppMeta();
    if (!meta) return reply.code(404).send({ error: "no_app" });
    try {
      const buf = await fs.readFile(appPath());
      reply.header("Cache-Control", "no-store");
      reply.header("Content-Disposition", 'attachment; filename="j2534_app.zip"');
      return reply.type("application/zip").send(buf);
    } catch {
      return reply.code(404).send({ error: "no_app" });
    }
  });

  app.post("/firmware/app", { preHandler: requireDeviceToken }, async (request, reply) => {
    const body = request.body;
    if (!Buffer.isBuffer(body) || body.length < 32 || body.length > APP_MAX) {
      return reply.code(400).send({ error: "bad_body" });
    }
    const entries = readZip(body);
    if (!entries) return reply.code(400).send({ error: "bad_zip" });
    const names = entries.map((e) => e.name);
    if (names.some((n) => n.endsWith("/") || n.includes("..") || n.includes("/") || !APP_ALLOW.has(n))) {
      return reply.code(400).send({ error: "bad_files" });
    }
    if (APP_NEED.some((n) => !names.includes(n))) return reply.code(400).send({ error: "bad_files" });
    const version = appVersionOf(entries);
    if (!version) return reply.code(400).send({ error: "no_version" });
    const meta: AppMeta = {
      present: true,
      version,
      size: body.length,
      uploadedAt: new Date().toISOString(),
      sha256: createHash("sha256").update(body).digest("hex"),
      files: names,
    };
    await fs.mkdir(env.firmwareDir, { recursive: true });
    const tmp = appPath() + ".tmp";
    await fs.writeFile(tmp, body);
    await fs.rename(tmp, appPath());
    await fs.writeFile(appMetaPath(), JSON.stringify(meta));
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
