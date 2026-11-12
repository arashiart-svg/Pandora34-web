import { createHash, randomBytes } from "crypto";
import { promises as fs } from "fs";
import path from "path";
import { FastifyInstance, FastifyRequest } from "fastify";
import { requireDeviceToken, requireWebGate } from "../auth/middleware";
import { env } from "../env";
import { prisma } from "../prisma";

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

function stampBoot(template: Buffer): { image: Buffer; id: string } | null {
  const at = template.indexOf("CFID");
  if (at < 0 || at + 20 > template.length) return null;
  for (let i = 0; i < 16; i++) {
    if (template[at + 4 + i] !== 0) return null;
  }
  const id = randomBytes(16);
  const image = Buffer.from(template);
  id.copy(image, at + 4);
  return { image, id: id.toString("hex") };
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

const ASK_TTL_MS = 24 * 60 * 60 * 1000;
const BURST_HOUR = 8;

class AskTaken extends Error {
  constructor() {
    super("taken");
  }
}

function clientIp(request: FastifyRequest): string {
  const real = request.headers["x-real-ip"];
  const raw = (Array.isArray(real) ? real[0] : real || "").toString().split(",")[0].trim();
  return (raw || request.ip || "").slice(0, 64);
}

function iso(value: Date | string | null | undefined): string {
  if (!value) return "";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toISOString();
}

let tablesReady: Promise<void> | null = null;

function ensureFilterTables(): Promise<void> {
  if (!tablesReady) {
    tablesReady = (async () => {
      await prisma.$executeRawUnsafe(`
        CREATE TABLE IF NOT EXISTS "FilterAsk" (
          "id" TEXT PRIMARY KEY,
          "label" TEXT NOT NULL,
          "status" TEXT NOT NULL,
          "ip" TEXT NOT NULL DEFAULT '',
          "bootId" TEXT,
          "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
          "expiresAt" TIMESTAMP(3) NOT NULL,
          "decidedAt" TIMESTAMP(3),
          "deliveredAt" TIMESTAMP(3)
        )`);
      await prisma.$executeRawUnsafe(`
        CREATE TABLE IF NOT EXISTS "FilterUnit" (
          "bootId" TEXT PRIMARY KEY,
          "label" TEXT NOT NULL,
          "status" TEXT NOT NULL,
          "askId" TEXT NOT NULL DEFAULT '',
          "firmware" TEXT NOT NULL DEFAULT '',
          "imageCount" INTEGER NOT NULL DEFAULT 0,
          "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
          "lastImageAt" TIMESTAMP(3)
        )`);
      await prisma.$executeRawUnsafe(`
        CREATE TABLE IF NOT EXISTS "FilterLog" (
          "id" TEXT PRIMARY KEY,
          "at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
          "kind" TEXT NOT NULL,
          "bootId" TEXT NOT NULL DEFAULT '',
          "askId" TEXT NOT NULL DEFAULT '',
          "ip" TEXT NOT NULL DEFAULT '',
          "result" TEXT NOT NULL,
          "detail" TEXT NOT NULL DEFAULT ''
        )`);
      await prisma.$executeRawUnsafe(`CREATE INDEX IF NOT EXISTS "FilterLog_at_idx" ON "FilterLog" ("at")`);
    })().catch((err) => {
      tablesReady = null;
      throw err;
    });
  }
  return tablesReady;
}

type AskRow = {
  id: string;
  label: string;
  status: string;
  ip: string;
  bootId: string | null;
  createdAt: Date;
  expiresAt: Date;
  decidedAt: Date | null;
  deliveredAt: Date | null;
};

type UnitRow = {
  bootId: string;
  label: string;
  status: string;
  askId: string;
  firmware: string;
  imageCount: number;
  createdAt: Date;
  lastImageAt: Date | null;
};

type LogRow = {
  id: string;
  at: Date;
  kind: string;
  bootId: string;
  askId: string;
  ip: string;
  result: string;
  detail: string;
};

async function journal(
  kind: string,
  result: string,
  ip: string,
  extra?: { bootId?: string; askId?: string; detail?: string }
): Promise<void> {
  await prisma.$executeRaw`
    INSERT INTO "FilterLog" ("id", "at", "kind", "bootId", "askId", "ip", "result", "detail")
    VALUES (
      ${randomBytes(16).toString("hex")},
      ${new Date()},
      ${kind},
      ${extra?.bootId || ""},
      ${extra?.askId || ""},
      ${ip.slice(0, 64)},
      ${result},
      ${(extra?.detail || "").slice(0, 200)}
    )`;
}

async function expireAsks(): Promise<void> {
  await prisma.$executeRaw`
    UPDATE "FilterAsk"
    SET "status" = 'expired'
    WHERE "status" IN ('pending', 'approved') AND "expiresAt" < ${new Date()}`;
}

async function noteBurst(ip: string): Promise<void> {
  const since = new Date(Date.now() - 60 * 60 * 1000);
  const rows = await prisma.$queryRaw<Array<{ n: bigint }>>`
    SELECT COUNT(*) AS n FROM "FilterAsk" WHERE "createdAt" >= ${since}`;
  const n = Number(rows[0]?.n || 0);
  if (n < BURST_HOUR) return;
  const seen = await prisma.$queryRaw<Array<{ id: string }>>`
    SELECT "id" FROM "FilterLog" WHERE "kind" = 'burst' AND "at" >= ${since} LIMIT 1`;
  if (seen.length) return;
  await journal("burst", "много заявок", ip, { detail: "за час много заявок, похоже софт расползся" });
}

async function readAsk(id: string): Promise<AskRow | null> {
  const rows = await prisma.$queryRaw<AskRow[]>`
    SELECT "id", "label", "status", "ip", "bootId", "createdAt", "expiresAt", "decidedAt", "deliveredAt"
    FROM "FilterAsk" WHERE "id" = ${id}`;
  return rows[0] || null;
}

function askView(row: AskRow): Record<string, string> {
  return {
    id: row.id,
    label: row.label,
    status: row.status,
    ip: row.ip,
    bootId: row.bootId || "",
    createdAt: iso(row.createdAt),
    expiresAt: iso(row.expiresAt),
  };
}

export async function firmwareRoutes(app: FastifyInstance): Promise<void> {
  await ensureFilterTables();
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
    return reply.code(403).send({ error: "boot_needs_approval" });
  });

  app.post("/firmware/boot/ask", { preHandler: requireDeviceToken }, async (request, reply) => {
    await ensureFilterTables();
    await expireAsks();
    const body = request.body as { label?: unknown };
    const label = String((body && body.label) || "").trim().slice(0, 80);
    if (!label) return reply.code(400).send({ error: "need_label" });
    const ip = clientIp(request);
    const id = randomBytes(16).toString("hex");
    const expiresAt = new Date(Date.now() + ASK_TTL_MS);
    await prisma.$executeRaw`
      INSERT INTO "FilterAsk" ("id", "label", "status", "ip", "createdAt", "expiresAt")
      VALUES (${id}, ${label}, 'pending', ${ip}, ${new Date()}, ${expiresAt})`;
    await journal("ask", "ждет", ip, { askId: id, detail: label });
    await noteBurst(ip);
    return { id, status: "pending", expiresAt: expiresAt.toISOString() };
  });

  app.get("/firmware/boot/ask/:id", { preHandler: requireDeviceToken }, async (request, reply) => {
    await ensureFilterTables();
    await expireAsks();
    const id = String((request.params as { id?: string }).id || "");
    if (!/^[0-9a-f]{32}$/.test(id)) return reply.code(404).send({ error: "no_ask" });
    const row = await readAsk(id);
    if (!row) return reply.code(404).send({ error: "no_ask" });
    return askView(row);
  });

  app.get("/firmware/boot/ask/:id/file", { preHandler: requireDeviceToken }, async (request, reply) => {
    await ensureFilterTables();
    await expireAsks();
    const id = String((request.params as { id?: string }).id || "");
    if (!/^[0-9a-f]{32}$/.test(id)) return reply.code(404).send({ error: "no_ask" });
    const row = await readAsk(id);
    if (!row) return reply.code(404).send({ error: "no_ask" });
    if (row.status !== "approved") {
      return reply.code(409).send({ error: "not_ready", status: row.status });
    }
    let stamped: { image: Buffer; id: string };
    try {
      const buf = await fs.readFile(bootPath());
      const made = stampBoot(buf);
      if (!made) return reply.code(409).send({ error: "boot_no_slot" });
      stamped = made;
    } catch {
      return reply.code(404).send({ error: "no_boot" });
    }
    const ip = clientIp(request);
    const now = new Date();
    try {
      await prisma.$transaction(async (tx: typeof prisma) => {
        const claimed = await tx.$executeRaw`
          UPDATE "FilterAsk"
          SET "status" = 'delivered', "bootId" = ${stamped.id}, "deliveredAt" = ${now}
          WHERE "id" = ${id} AND "status" = 'approved'`;
        if (claimed !== 1) throw new AskTaken();
        await tx.$executeRaw`
          INSERT INTO "FilterUnit" ("bootId", "label", "status", "askId", "firmware", "imageCount", "createdAt")
          VALUES (${stamped.id}, ${row.label}, 'active', ${id}, '', 0, ${now})`;
        await tx.$executeRaw`
          INSERT INTO "FilterLog" ("id", "at", "kind", "bootId", "askId", "ip", "result", "detail")
          VALUES (
            ${randomBytes(16).toString("hex")},
            ${now},
            'deliver',
            ${stamped.id},
            ${id},
            ${ip},
            'отдан загрузчик',
            ${row.label}
          )`;
      });
    } catch (err) {
      const taken = err instanceof AskTaken || (err instanceof Error && err.message.includes("taken"));
      if (taken) {
        const again = await readAsk(id);
        return reply.code(409).send({ error: "not_ready", status: again?.status || "delivered" });
      }
      throw err;
    }
    reply.header("Cache-Control", "no-store");
    reply.header("X-Boot-Id", stamped.id);
    reply.header("Content-Disposition", 'attachment; filename="boot.bin"');
    return reply.type("application/octet-stream").send(stamped.image);
  });

  app.get("/firmware/desk", { preHandler: requireWebGate }, async () => {
    await ensureFilterTables();
    await expireAsks();
    const asks: AskRow[] = await prisma.$queryRaw`
      SELECT "id", "label", "status", "ip", "bootId", "createdAt", "expiresAt", "decidedAt", "deliveredAt"
      FROM "FilterAsk" ORDER BY "createdAt" DESC LIMIT 40`;
    const filters: UnitRow[] = await prisma.$queryRaw`
      SELECT "bootId", "label", "status", "askId", "firmware", "imageCount", "createdAt", "lastImageAt"
      FROM "FilterUnit" ORDER BY "createdAt" DESC LIMIT 200`;
    const journalRows: LogRow[] = await prisma.$queryRaw`
      SELECT "id", "at", "kind", "bootId", "askId", "ip", "result", "detail"
      FROM "FilterLog" ORDER BY "at" DESC LIMIT 80`;
    return {
      asks: asks.map((row: AskRow) => askView(row)),
      filters: filters.map((row: UnitRow) => ({
        bootId: row.bootId,
        label: row.label,
        status: row.status,
        firmware: row.firmware,
        imageCount: Number(row.imageCount || 0),
        createdAt: iso(row.createdAt),
        lastImageAt: iso(row.lastImageAt),
      })),
      journal: journalRows.map((row: LogRow) => ({
        at: iso(row.at),
        kind: row.kind,
        bootId: row.bootId,
        ip: row.ip,
        result: row.result,
        detail: row.detail,
      })),
    };
  });

  app.post("/firmware/desk/ask/:id/allow", { preHandler: requireWebGate }, async (request, reply) => {
    await ensureFilterTables();
    await expireAsks();
    const id = String((request.params as { id?: string }).id || "");
    if (!/^[0-9a-f]{32}$/.test(id)) return reply.code(404).send({ error: "no_ask" });
    const now = new Date();
    const n = await prisma.$executeRaw`
      UPDATE "FilterAsk" SET "status" = 'approved', "decidedAt" = ${now}
      WHERE "id" = ${id} AND "status" = 'pending'`;
    if (n !== 1) return reply.code(409).send({ error: "not_pending" });
    const row = await readAsk(id);
    await journal("allow", "разрешена", clientIp(request), { askId: id, detail: row?.label || "" });
    return { ok: true };
  });

  app.post("/firmware/desk/ask/:id/deny", { preHandler: requireWebGate }, async (request, reply) => {
    await ensureFilterTables();
    await expireAsks();
    const id = String((request.params as { id?: string }).id || "");
    if (!/^[0-9a-f]{32}$/.test(id)) return reply.code(404).send({ error: "no_ask" });
    const now = new Date();
    const n = await prisma.$executeRaw`
      UPDATE "FilterAsk" SET "status" = 'denied', "decidedAt" = ${now}
      WHERE "id" = ${id} AND "status" = 'pending'`;
    if (n !== 1) return reply.code(409).send({ error: "not_pending" });
    const row = await readAsk(id);
    await journal("deny", "отказана", clientIp(request), { askId: id, detail: row?.label || "" });
    return { ok: true };
  });

  app.post("/firmware/desk/filter/:bootId/ban", { preHandler: requireWebGate }, async (request, reply) => {
    await ensureFilterTables();
    const bootId = String((request.params as { bootId?: string }).bootId || "").toLowerCase();
    if (!/^[0-9a-f]{32}$/.test(bootId)) return reply.code(404).send({ error: "no_filter" });
    const n = await prisma.$executeRaw`
      UPDATE "FilterUnit" SET "status" = 'banned' WHERE "bootId" = ${bootId} AND "status" = 'active'`;
    if (n !== 1) return reply.code(409).send({ error: "not_active" });
    await journal("ban", "запрещён", clientIp(request), { bootId });
    return { ok: true };
  });

  app.post("/firmware/desk/filter/:bootId/unban", { preHandler: requireWebGate }, async (request, reply) => {
    await ensureFilterTables();
    const bootId = String((request.params as { bootId?: string }).bootId || "").toLowerCase();
    if (!/^[0-9a-f]{32}$/.test(bootId)) return reply.code(404).send({ error: "no_filter" });
    const n = await prisma.$executeRaw`
      UPDATE "FilterUnit" SET "status" = 'active' WHERE "bootId" = ${bootId} AND "status" = 'banned'`;
    if (n !== 1) return reply.code(409).send({ error: "not_banned" });
    await journal("unban", "снова разрешён", clientIp(request), { bootId });
    return { ok: true };
  });

  app.post("/firmware/boot", { preHandler: [requireDeviceToken, requireWebGate] }, async (request, reply) => {
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

  app.get("/firmware/image", { preHandler: requireDeviceToken }, async (request, reply) => {
    await ensureFilterTables();
    const ip = clientIp(request);
    const rawId = request.headers["x-boot-id"];
    const bootId = (Array.isArray(rawId) ? rawId[0] : rawId || "").toString().trim().toLowerCase();
    if (!/^[0-9a-f]{32}$/.test(bootId)) {
      await journal("image", "прошивку не отдал", ip, { detail: "нет номера" });
      return reply.code(403).send({ error: "need_boot_id" });
    }
    const units = await prisma.$queryRaw<UnitRow[]>`
      SELECT "bootId", "label", "status", "askId", "firmware", "imageCount", "createdAt", "lastImageAt"
      FROM "FilterUnit" WHERE "bootId" = ${bootId}`;
    const unit = units[0];
    if (!unit) {
      await journal("image", "прошивку не отдал", ip, { bootId, detail: "номер не из базы" });
      return reply.code(403).send({ error: "unknown_boot" });
    }
    if (unit.status !== "active") {
      await journal("image", "прошивку не отдал", ip, { bootId, detail: "фильтр запрещён" });
      return reply.code(403).send({ error: "banned" });
    }
    const meta = await readImageMeta();
    if (!meta) return reply.code(404).send({ error: "no_image" });
    try {
      const buf = await fs.readFile(imagePath());
      const now = new Date();
      await prisma.$executeRaw`
        UPDATE "FilterUnit"
        SET "firmware" = ${meta.version}, "lastImageAt" = ${now}, "imageCount" = "imageCount" + 1
        WHERE "bootId" = ${bootId}`;
      await journal("image", "отдана прошивка", ip, { bootId, detail: meta.version || "" });
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
