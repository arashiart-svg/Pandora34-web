import { createCipheriv, createHash, randomBytes, timingSafeEqual } from "crypto";
import { crc32 } from "zlib";
import { promises as fs } from "fs";
import path from "path";
import { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import jwt from "jsonwebtoken";
import { clearWebSessionCookie, loginBlocked, loginFailed, loginIp, loginSucceeded, webSessionCookie } from "../auth/login-guard";
import { verifyPassword } from "../auth/password";
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

function stampBoot(template: Buffer): { image: Buffer; id: string; key: string } | null {
  const at = template.indexOf("CFID");
  if (at < 0 || at + 20 > template.length) return null;
  for (let i = 0; i < 16; i++) {
    if (template[at + 4 + i] !== 0) return null;
  }
  const image = Buffer.from(template);
  const id = randomBytes(16);
  id.copy(image, at + 4);
  let key = "";
  let ky = image.indexOf("CFKY");
  while (ky >= 0 && Math.abs(ky - at) < 20) ky = image.indexOf("CFKY", ky + 1);
  if (ky >= 0 && ky + 20 <= image.length) {
    for (let i = 0; i < 16; i++) {
      if (image[ky + 4 + i] !== 0) return null;
    }
    const rawKey = randomBytes(16);
    rawKey.copy(image, ky + 4);
    key = rawKey.toString("hex");
  }
  return { image, id: id.toString("hex"), key };
}

function wrapForKey(file: Buffer, keyHex: string): Buffer | null {
  if (file.length <= IMAGE_OFF + 8) return null;
  const plain = file.subarray(IMAGE_OFF, Math.min(file.length, IMAGE_MAX));
  if (plain.length < 8) return null;
  const key = Buffer.from(keyHex, "hex");
  const iv = randomBytes(16);
  const pad = (16 - (plain.length % 16)) % 16;
  const src = pad ? Buffer.concat([plain, Buffer.alloc(pad)]) : plain;
  const cipher = createCipheriv("aes-128-ctr", key, iv);
  const ct = Buffer.concat([cipher.update(src), cipher.final()]);
  const head = Buffer.alloc(28);
  head.write("CFEN", 0, "ascii");
  head.writeUInt32LE(plain.length, 4);
  head.writeUInt32LE(crc32(plain) >>> 0, 8);
  iv.copy(head, 12);
  return Buffer.concat([head, ct]);
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

const EXE_KEEP = 10;
const EXE_LIMIT = 200 * 1024 * 1024;

type ExeVer = {
  version: string;
  size: number;
  sha256: string;
  uploadedAt: string;
  file: string;
};

type ExeMeta = ExeVer & {
  present: true;
  versions: ExeVer[];
};

function exeDir(): string {
  return path.join(env.firmwareDir, "appexe");
}

function exeMetaPath(): string {
  return path.join(exeDir(), "current.json");
}

function exeVersionOk(version: string): boolean {
  return /^\d+(?:\.\d+){0,3}$/.test(version);
}

async function readExeMeta(): Promise<ExeMeta | null> {
  try {
    const raw = await fs.readFile(exeMetaPath(), "utf8");
    const parsed = JSON.parse(raw) as ExeMeta;
    if (!parsed || parsed.present !== true || !exeVersionOk(parsed.version || "")) return null;
    if (!Array.isArray(parsed.versions)) return null;
    return parsed;
  } catch {
    return null;
  }
}

async function writeExe(version: string, body: Buffer): Promise<ExeMeta> {
  const file = `${version}.exe`;
  const item: ExeVer = {
    version,
    size: body.length,
    sha256: createHash("sha256").update(body).digest("hex"),
    uploadedAt: new Date().toISOString(),
    file,
  };
  await fs.mkdir(exeDir(), { recursive: true });
  const tmp = path.join(exeDir(), `${file}.tmp`);
  await fs.writeFile(tmp, body);
  await fs.rename(tmp, path.join(exeDir(), file));
  const prev = await readExeMeta();
  let versions = (prev?.versions || []).filter((row) => row.version !== version && exeVersionOk(row.version));
  versions.unshift(item);
  versions.sort((a, b) => (a.uploadedAt < b.uploadedAt ? 1 : a.uploadedAt > b.uploadedAt ? -1 : 0));
  const dropped = versions.slice(EXE_KEEP);
  versions = versions.slice(0, EXE_KEEP);
  for (const old of dropped) {
    if (!old.file || old.file.includes("/") || old.file.includes("\\") || old.file === file) continue;
    await fs.rm(path.join(exeDir(), old.file), { force: true });
  }
  const meta: ExeMeta = { present: true, ...item, versions };
  const metaTmp = exeMetaPath() + ".tmp";
  await fs.writeFile(metaTmp, JSON.stringify(meta));
  await fs.rename(metaTmp, exeMetaPath());
  return meta;
}

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
      await prisma.$executeRawUnsafe(
        `ALTER TABLE "FilterUnit" ADD COLUMN IF NOT EXISTS "imageKey" TEXT NOT NULL DEFAULT ''`,
      );
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

function readCookie(header: string | undefined, name: string): string {
  const raw = header || "";
  for (const part of raw.split(";")) {
    const eq = part.indexOf("=");
    if (eq < 0) continue;
    if (part.slice(0, eq).trim() !== name) continue;
    try {
      return decodeURIComponent(part.slice(eq + 1).trim());
    } catch {
      return "";
    }
  }
  return "";
}

function sameSecret(got: string, want: string): boolean {
  const a = Buffer.from(got);
  const b = Buffer.from(want);
  if (a.length === 0 || a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}

function siteCookieOk(token: string): boolean {
  try {
    jwt.verify(token, env.jwtSecret);
    return true;
  } catch {
    return false;
  }
}

function bearerOf(request: FastifyRequest): string {
  const header = request.headers.authorization;
  if (!header || !header.startsWith("Bearer ")) return "";
  return header.slice("Bearer ".length).trim();
}

const WEB_MAX_MS = 7 * 24 * 60 * 60 * 1000;

function webGateFresh(payload: { iat?: number }): boolean {
  return typeof payload.iat === "number" && payload.iat * 1000 > Date.now() - WEB_MAX_MS;
}

function deskCookieOk(token: string): boolean {
  try {
    const payload = jwt.verify(token, env.jwtSecret) as { kind?: string; desk?: boolean; iat?: number };
    return payload.kind === "web-gate" && payload.desk === true && webGateFresh(payload);
  } catch {
    return false;
  }
}

function deskOk(request: FastifyRequest): boolean {
  const cookie = readCookie(request.headers.cookie, "pandora_web");
  if (cookie && deskCookieOk(cookie)) return true;
  const desk = (process.env.DESK_TOKEN || "").trim();
  const bearer = bearerOf(request);
  return Boolean(desk && bearer && sameSecret(bearer, desk));
}

function windowOk(request: FastifyRequest): boolean {
  const want = (process.env.WINDOW_TOKEN || "").trim();
  const got = bearerOf(request);
  return Boolean(want && got && sameSecret(got, want));
}

/** Стол CRM: кука входа на сайт или отдельный ключ из .env. Ключ окна сюда не проходит. */
async function requireDesk(request: FastifyRequest, reply: FastifyReply): Promise<void> {
  if (deskOk(request)) return;
  reply.code(401).send({ error: "desk_denied" });
}

/** Окно J2534. Этот ключ не открывает базу клиентов. */
async function requireWindow(request: FastifyRequest, reply: FastifyReply): Promise<void> {
  if (windowOk(request)) return;
  reply.code(401).send({ error: "unauthorized_device" });
}

async function requireWindowOrDesk(request: FastifyRequest, reply: FastifyReply): Promise<void> {
  if (windowOk(request) || deskOk(request)) return;
  reply.code(401).send({ error: "unauthorized_device" });
}

function gateCookieOk(token: string): boolean {
  try {
    const payload = jwt.verify(token, env.jwtSecret) as { kind?: string; iat?: number };
    return payload.kind === "web-gate" && webGateFresh(payload);
  } catch {
    return false;
  }
}

function envClean(name: string, fallback = ""): string {
  let value = (process.env[name] ?? fallback).replace(/\r/g, "").trim();
  if (value.length >= 2) {
    const quote = value[0];
    if ((quote === '"' || quote === "'") && value.endsWith(quote)) value = value.slice(1, -1);
  }
  return value;
}

async function doorPasswordOk(username: string, password: string): Promise<"" | "desk" | "gate"> {
  const user = username.trim();
  const pass = password.replace(/\r/g, "");
  if (!user || !pass) return "";
  const pairs: Array<[string, string]> = [
    [envClean("WEB_GATE_USER", env.adminUsername), envClean("WEB_GATE_PASSWORD")],
    [envClean("ADMIN_USERNAME", env.adminUsername), envClean("ADMIN_PASSWORD", env.adminPassword)],
  ];
  for (const [wantUser, wantPass] of pairs) {
    if (!wantUser || !wantPass) continue;
    if (sameSecret(user, wantUser) && sameSecret(pass, wantPass)) return "desk";
  }
  const row = await prisma.user.findUnique({ where: { username: user } });
  if (row && (await verifyPassword(pass, row.passwordHash))) return row.role === "OWNER" ? "desk" : "gate";
  if (await crmMasterOk(user, pass)) return "gate";
  return "";
}

function crmPassHash(password: string): string {
  return createHash("sha256").update("pandora34:" + password).digest("hex");
}

async function crmMasterOk(username: string, password: string): Promise<boolean> {
  const login = username.trim().toLowerCase();
  if (!login || login === "admin") return false;
  const hash = crmPassHash(password);
  const saved = await prisma.appState.findUnique({ where: { id: "main" } });
  const raw = saved?.data as unknown;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return false;
  let masters = (raw as { masters?: unknown }).masters;
  if (typeof masters === "string") {
    try {
      masters = JSON.parse(masters);
    } catch {
      return false;
    }
  }
  if (!Array.isArray(masters)) return false;
  for (const item of masters) {
    if (!item || typeof item !== "object") continue;
    const master = item as { login?: unknown; passwordHash?: unknown; accessEnabled?: unknown };
    if (!master.accessEnabled) continue;
    const masterLogin = String(master.login || "").trim().toLowerCase();
    const masterHash = String(master.passwordHash || "");
    if (!masterLogin || !masterHash) continue;
    if (masterLogin === login && sameSecret(hash, masterHash)) return true;
  }
  return false;
}

function gateCookie(token: string): string {
  return webSessionCookie(token);
}

export async function firmwareRoutes(app: FastifyInstance): Promise<void> {
  await ensureFilterTables();

  try {
    app.addContentTypeParser("application/x-www-form-urlencoded", { parseAs: "string" }, (_req, body, done) => {
      try {
        done(null, Object.fromEntries(new URLSearchParams(String(body))));
      } catch (err) {
        done(err as Error);
      }
    });
  } catch {
    // разбор формы уже включён
  }

  app.post("/auth/web-login-form", async (request, reply) => {
    const body = (request.body || {}) as { username?: string; password?: string };
    const username = String(body.username || "");
    const password = String(body.password || "");
    const ip = loginIp(request);
    if (loginBlocked(ip, username)) {
      return reply.code(303).redirect("/enter.html?err=2");
    }
    const door = await doorPasswordOk(username, password);
    if (!door) {
      loginFailed(ip, username);
      return reply.code(303).redirect("/enter.html?err=1");
    }
    loginSucceeded(ip, username);
    const token = jwt.sign(
      { sub: "web-gate", username: username.trim(), role: "ADMIN", kind: "web-gate", desk: door === "desk" },
      env.jwtSecret,
      { expiresIn: "7d" }
    );
    reply.header("Set-Cookie", gateCookie(token));
    return reply.code(303).redirect("/");
  });

  app.get("/auth/web-check", async (request, reply) => {
    const token = readCookie(request.headers.cookie, "pandora_web");
    if (!token || !gateCookieOk(token)) return reply.code(401).send();
    return reply.code(200).send();
  });

  app.get("/auth/web-logout", async (_request, reply) => {
    reply.header("Set-Cookie", clearWebSessionCookie());
    return reply.code(303).redirect("/enter.html");
  });
  app.get("/firmware/boot/info", { preHandler: requireWindowOrDesk }, async () => {
    const meta = await readMeta();
    if (!meta) return { present: false };
    try {
      const buf = await fs.readFile(bootPath());
      return { ...meta, version: bootVersion(buf) };
    } catch {
      return { present: false };
    }
  });

  app.get("/firmware/boot", { preHandler: requireWindow }, async (_request, reply) => {
    return reply.code(403).send({ error: "boot_needs_approval" });
  });

  app.post("/firmware/boot/ask", { preHandler: requireWindow }, async (request, reply) => {
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

  app.get("/firmware/boot/ask/:id", { preHandler: requireWindow }, async (request, reply) => {
    await ensureFilterTables();
    await expireAsks();
    const id = String((request.params as { id?: string }).id || "");
    if (!/^[0-9a-f]{32}$/.test(id)) return reply.code(404).send({ error: "no_ask" });
    const row = await readAsk(id);
    if (!row) return reply.code(404).send({ error: "no_ask" });
    return askView(row);
  });

  app.get("/firmware/boot/ask/:id/file", { preHandler: requireWindow }, async (request, reply) => {
    await ensureFilterTables();
    await expireAsks();
    const id = String((request.params as { id?: string }).id || "");
    if (!/^[0-9a-f]{32}$/.test(id)) return reply.code(404).send({ error: "no_ask" });
    const row = await readAsk(id);
    if (!row) return reply.code(404).send({ error: "no_ask" });
    if (row.status !== "approved") {
      return reply.code(409).send({ error: "not_ready", status: row.status });
    }
    let stamped: { image: Buffer; id: string; key: string };
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
          INSERT INTO "FilterUnit" ("bootId", "label", "status", "askId", "firmware", "imageCount", "imageKey", "createdAt")
          VALUES (${stamped.id}, ${row.label}, 'active', ${id}, '', 0, ${stamped.key}, ${now})`;
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

  app.get("/firmware/desk", { preHandler: requireDesk }, async () => {
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

  app.post("/firmware/desk/ask/:id/allow", { preHandler: requireDesk }, async (request, reply) => {
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

  app.post("/firmware/desk/ask/:id/deny", { preHandler: requireDesk }, async (request, reply) => {
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

  app.post("/firmware/desk/filter/:bootId/ban", { preHandler: requireDesk }, async (request, reply) => {
    await ensureFilterTables();
    const bootId = String((request.params as { bootId?: string }).bootId || "").toLowerCase();
    if (!/^[0-9a-f]{32}$/.test(bootId)) return reply.code(404).send({ error: "no_filter" });
    const n = await prisma.$executeRaw`
      UPDATE "FilterUnit" SET "status" = 'banned' WHERE "bootId" = ${bootId} AND "status" = 'active'`;
    if (n !== 1) return reply.code(409).send({ error: "not_active" });
    await journal("ban", "запрещён", clientIp(request), { bootId });
    return { ok: true };
  });

  app.post("/firmware/desk/clear", { preHandler: requireDesk }, async () => {
    await ensureFilterTables();
    await prisma.$executeRaw`DELETE FROM "FilterLog"`;
    await prisma.$executeRaw`DELETE FROM "FilterUnit"`;
    await prisma.$executeRaw`DELETE FROM "FilterAsk"`;
    return { ok: true };
  });

  app.post("/firmware/desk/filter/:bootId/unban", { preHandler: requireDesk }, async (request, reply) => {
    await ensureFilterTables();
    const bootId = String((request.params as { bootId?: string }).bootId || "").toLowerCase();
    if (!/^[0-9a-f]{32}$/.test(bootId)) return reply.code(404).send({ error: "no_filter" });
    const n = await prisma.$executeRaw`
      UPDATE "FilterUnit" SET "status" = 'active' WHERE "bootId" = ${bootId} AND "status" = 'banned'`;
    if (n !== 1) return reply.code(409).send({ error: "not_banned" });
    await journal("unban", "снова разрешён", clientIp(request), { bootId });
    return { ok: true };
  });

  app.post("/firmware/boot", { preHandler: requireDesk }, async (request, reply) => {
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

  app.get("/firmware/app/info", { preHandler: requireWindowOrDesk }, async () => {
    const meta = await readAppMeta();
    if (!meta) return { present: false };
    try {
      await fs.access(appDir());
    } catch {
      return { present: false };
    }
    return meta;
  });

  app.get("/firmware/app/file/:name", { preHandler: requireWindow }, async (request, reply) => {
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

  app.post("/firmware/app", { preHandler: requireDesk }, async (request, reply) => {
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

  app.get("/firmware/app/exe/info", { preHandler: requireWindowOrDesk }, async () => {
    const meta = await readExeMeta();
    if (!meta) return { present: false };
    try {
      await fs.access(path.join(exeDir(), meta.file));
    } catch {
      return { present: false };
    }
    return meta;
  });

  app.get("/firmware/app/exe", { preHandler: requireWindow }, async (_request, reply) => {
    const meta = await readExeMeta();
    if (!meta) return reply.code(404).send({ error: "no_file" });
    try {
      const buf = await fs.readFile(path.join(exeDir(), meta.file));
      reply.header("Cache-Control", "no-store");
      reply.header("X-App-Version", meta.version);
      reply.header("X-App-Sha256", meta.sha256);
      return reply.type("application/octet-stream").send(buf);
    } catch {
      return reply.code(404).send({ error: "no_file" });
    }
  });

  app.get("/firmware/app/exe/:version", { preHandler: requireWindow }, async (request, reply) => {
    const version = (request.params as { version?: string }).version || "";
    if (!exeVersionOk(version)) return reply.code(404).send({ error: "no_file" });
    const meta = await readExeMeta();
    const item = meta?.versions.find((row) => row.version === version);
    if (!item) return reply.code(404).send({ error: "no_file" });
    try {
      const buf = await fs.readFile(path.join(exeDir(), item.file));
      reply.header("Cache-Control", "no-store");
      reply.header("X-App-Version", item.version);
      reply.header("X-App-Sha256", item.sha256);
      return reply.type("application/octet-stream").send(buf);
    } catch {
      return reply.code(404).send({ error: "no_file" });
    }
  });

  app.post(
    "/firmware/app/exe",
    { bodyLimit: EXE_LIMIT, preHandler: requireDesk },
    async (request, reply) => {
      const body = request.body;
      if (!Buffer.isBuffer(body) || body.length < 2 || body[0] !== 0x4d || body[1] !== 0x5a) {
        return reply.code(400).send({ error: "bad_body" });
      }
      if (body.length > EXE_LIMIT) return reply.code(400).send({ error: "bad_body" });
      const rawVer = request.headers["x-app-version"];
      const version = (Array.isArray(rawVer) ? rawVer[0] : rawVer || "").toString().trim();
      if (!exeVersionOk(version)) return reply.code(400).send({ error: "no_version" });
      return writeExe(version, body);
    },
  );

  app.get("/firmware/image/info", { preHandler: requireWindowOrDesk }, async () => {
    const meta = await readImageMeta();
    if (!meta) return { present: false };
    try {
      await fs.access(imagePath());
    } catch {
      return { present: false };
    }
    return meta;
  });

  app.get("/firmware/image", { preHandler: requireWindow }, async (request, reply) => {
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
      const keys = await prisma.$queryRaw<Array<{ imageKey: string }>>`
        SELECT "imageKey" FROM "FilterUnit" WHERE "bootId" = ${bootId}`;
      const imageKey = String(keys[0]?.imageKey || "").trim().toLowerCase();
      let body: Buffer = buf;
      let closed = false;
      if (imageKey) {
        if (!/^[0-9a-f]{32}$/.test(imageKey)) {
          return reply.code(500).send({ error: "bad_key" });
        }
        const wrapped = wrapForKey(buf, imageKey);
        if (!wrapped) return reply.code(500).send({ error: "bad_image" });
        body = wrapped;
        closed = true;
      }
      const now = new Date();
      await prisma.$executeRaw`
        UPDATE "FilterUnit"
        SET "firmware" = ${meta.version}, "lastImageAt" = ${now}, "imageCount" = "imageCount" + 1
        WHERE "bootId" = ${bootId}`;
      await journal("image", closed ? "отдана закрытая прошивка" : "отдана прошивка", ip, {
        bootId,
        detail: meta.version || "",
      });
      reply.header("Cache-Control", "no-store");
      reply.header("Content-Disposition", 'attachment; filename="firmware.bin"');
      if (closed) reply.header("X-Image-Enc", "1");
      return reply.type("application/octet-stream").send(body);
    } catch {
      return reply.code(404).send({ error: "no_image" });
    }
  });

  app.post("/firmware/image", { preHandler: requireDesk }, async (request, reply) => {
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
