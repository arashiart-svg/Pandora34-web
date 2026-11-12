import { FastifyReply, FastifyRequest } from "fastify";
import jwt from "jsonwebtoken";
import { env } from "../env";
import { UserTokenPayload, verifyUserToken } from "./jwt";

function extractBearer(request: FastifyRequest): string | null {
  const header = request.headers.authorization;
  if (!header || !header.startsWith("Bearer ")) return null;
  return header.slice("Bearer ".length).trim();
}

function extractQueryToken(request: FastifyRequest): string | null {
  const q = request.query as { token?: unknown };
  const token = q && typeof q.token === "string" ? q.token.trim() : "";
  return token || null;
}

export function extractDeviceToken(request: FastifyRequest): string | null {
  return extractBearer(request) || extractQueryToken(request);
}

function readCookie(header: string | undefined, name: string): string {
  const raw = header || "";
  for (const part of raw.split(";")) {
    const [k, ...rest] = part.trim().split("=");
    if (k === name) return decodeURIComponent(rest.join("="));
  }
  return "";
}

function jwtKind(token: string): string {
  try {
    const payload = jwt.verify(token, env.jwtSecret) as { kind?: string };
    return payload.kind || "";
  } catch {
    return "";
  }
}

export function signDeskToken(): string {
  return jwt.sign({ kind: "cfs-desk" }, env.jwtSecret, { expiresIn: "12h" });
}

function deskBearerOk(request: FastifyRequest): boolean {
  const header = request.headers.authorization;
  if (!header || !header.startsWith("Bearer ")) return false;
  return jwtKind(header.slice("Bearer ".length).trim()) === "cfs-desk";
}

/** Вход на сайт или отдельный допуск заявок. Секрет приложения это не заменяет. */
export async function requireWebGate(request: FastifyRequest, reply: FastifyReply): Promise<void> {
  const cookie = readCookie(request.headers.cookie, "pandora_web");
  if ((cookie && jwtKind(cookie)) || deskBearerOk(request)) return;
  reply.code(401).send({ error: "need_password" });
}

/** Проверяет общий секрет устройства (Android-приложение), которым раньше был SUPABASE_KEY. */
export async function requireDeviceToken(request: FastifyRequest, reply: FastifyReply): Promise<void> {
  const token = extractDeviceToken(request);
  if (!token || token !== env.syncToken) {
    reply.code(401).send({ error: "unauthorized_device" });
    return;
  }
}

/** Проверяет общий секрет для внутренних вызовов ботов к API. */
export async function requireBotToken(request: FastifyRequest, reply: FastifyReply): Promise<void> {
  const token = extractBearer(request);
  if (!token || token !== env.botInternalToken) {
    reply.code(401).send({ error: "unauthorized_bot" });
  }
}

declare module "fastify" {
  interface FastifyRequest {
    user?: UserTokenPayload;
  }
}

/** Проверяет JWT сотрудника/владельца (для будущей веб-панели и защищённых отчётов). */
export async function requireUser(request: FastifyRequest, reply: FastifyReply): Promise<void> {
  const token = extractBearer(request);
  if (!token) {
    reply.code(401).send({ error: "unauthorized" });
    return;
  }
  try {
    request.user = verifyUserToken(token);
  } catch {
    reply.code(401).send({ error: "invalid_token" });
  }
}

export function requireRole(...roles: Array<UserTokenPayload["role"]>) {
  return async function (request: FastifyRequest, reply: FastifyReply): Promise<void> {
    if (!request.user || !roles.includes(request.user.role)) {
      reply.code(403).send({ error: "forbidden" });
    }
  };
}
