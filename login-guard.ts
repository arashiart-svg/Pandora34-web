import { FastifyRequest } from "fastify";

const WINDOW_MS = 15 * 60 * 1000;
const MAX_FAILS = 5;

type Bucket = { fails: number; resetAt: number };

const buckets = new Map<string, Bucket>();

function take(key: string): Bucket {
  const now = Date.now();
  const row = buckets.get(key);
  if (!row || row.resetAt <= now) {
    const fresh = { fails: 0, resetAt: now + WINDOW_MS };
    buckets.set(key, fresh);
    return fresh;
  }
  return row;
}

function keys(ip: string, username: string): string[] {
  const out = [`ip:${ip || "?"}`];
  const name = username.trim().toLowerCase();
  if (name) out.push(`user:${name}`);
  return out;
}

function prune(): void {
  if (buckets.size < 4000) return;
  const now = Date.now();
  for (const [key, row] of buckets) {
    if (row.resetAt <= now) buckets.delete(key);
  }
}

export function loginIp(request: FastifyRequest): string {
  const real = request.headers["x-real-ip"];
  const raw = (Array.isArray(real) ? real[0] : real || "").toString().split(",")[0].trim();
  return (raw || request.ip || "").slice(0, 64);
}

export function loginBlocked(ip: string, username: string): boolean {
  const now = Date.now();
  for (const key of keys(ip, username)) {
    const row = buckets.get(key);
    if (row && row.resetAt > now && row.fails >= MAX_FAILS) return true;
  }
  return false;
}

export function loginFailed(ip: string, username: string): void {
  for (const key of keys(ip, username)) {
    take(key).fails += 1;
  }
  prune();
}

export function loginSucceeded(ip: string, username: string): void {
  for (const key of keys(ip, username)) buckets.delete(key);
}

const WEEK = 7 * 24 * 60 * 60;

export function webSessionCookie(token: string): string {
  return `pandora_web=${encodeURIComponent(token)}; Path=/; Max-Age=${WEEK}; HttpOnly; Secure; SameSite=Lax`;
}

export function clearWebSessionCookie(): string {
  return "pandora_web=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax";
}
