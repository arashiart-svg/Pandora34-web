import { FastifyInstance } from "fastify";
import { requireDeviceToken } from "../auth/middleware";
import { getAppState, replaceAppState } from "../services/stateStore";
import { asAppState, CarRecord } from "../domain/state";
import { prisma } from "../prisma";
import { notifyClient, notifyStaff } from "../services/notify";

function carKey(c: CarRecord): string {
  if (c.id !== undefined && c.id !== null && String(c.id) !== "") return `id:${c.id}`;
  return `car:${c.number}|${c.brand}|${c.model}|${c.inAt}`;
}

/** Шлёт уведомление о готовности авто клиенту (если он писал боту) и сотрудникам. */
async function notifyNewlyDoneCars(before: CarRecord[], after: CarRecord[]): Promise<void> {
  const beforeMap = new Map(before.map((c) => [carKey(c), c]));
  for (const car of after) {
    if (car.status !== "done") continue;
    const prev = beforeMap.get(carKey(car));
    if (prev && prev.status === "done") continue; // уже было выдано, не новое событие

    const title = [car.brand, car.model].filter(Boolean).join(" ") || "Автомобиль";
    const text = `✅ Автомобиль готов: <b>${car.number ?? "без номера"}</b> (${title})`;
    void notifyStaff(text);

    const phone = normalizePhone(car.ownerPhone);
    if (!phone) continue;
    const link = await prisma.telegramClientLink.findFirst({ where: { phone } });
    if (link) {
      void notifyClient(link.chatId, `Здравствуйте! Ваш автомобиль ${car.number ?? ""} готов, можно забирать.`);
    }
  }
}

function normalizePhone(phone: unknown): string | null {
  if (!phone || typeof phone !== "string") return null;
  const digits = phone.replace(/\D/g, "");
  if (digits.length < 10) return null;
  return digits.slice(-10);
}

export async function syncRoutes(app: FastifyInstance): Promise<void> {
  // Совместимо по форме ответа с прежним запросом к Supabase
  // (GET .../app_state?id=eq.main&select=id,data,updated_at).
  app.get("/sync/state", { preHandler: requireDeviceToken }, async () => {
    const { data, version } = await getAppState();
    return { id: "main", data, updated_at: new Date().toISOString(), version };
  });

  // Совместимо с прежним POST в Supabase (upsert всей строки app_state).
  // Приложение уже присылает результат mergePayloads(local, remote) — сервер просто сохраняет его.
  app.post("/sync/state", { preHandler: requireDeviceToken }, async (request, reply) => {
    const body = request.body as { id?: string; data?: unknown };
    if (!body || typeof body !== "object" || !body.data) {
      return reply.code(400).send({ error: "invalid_body" });
    }

    const before = await getAppState();
    const newState = asAppState(body.data);
    const saved = await replaceAppState(newState);

    void notifyNewlyDoneCars(before.data.cars, saved.data.cars).catch((err) =>
      app.log.error(err, "notifyNewlyDoneCars failed")
    );

    return [{ id: "main", data: saved.data, updated_at: new Date().toISOString() }];
  });

  /** Прокси для секретной .ics-ссылки Google Календаря (браузер упирается в CORS). */
  app.post("/sync/calendar-ics", { preHandler: requireDeviceToken }, async (request, reply) => {
    const body = request.body as { url?: string };
    const url = String(body?.url || "").trim();
    if (!url) return reply.code(400).send({ error: "url_required" });

    let parsed: URL;
    try {
      parsed = new URL(url);
    } catch {
      return reply.code(400).send({ error: "invalid_url" });
    }
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
      return reply.code(400).send({ error: "invalid_protocol" });
    }
    const host = parsed.hostname.toLowerCase();
    const allowed =
      host === "calendar.google.com" ||
      host.endsWith(".google.com") ||
      host === "www.google.com";
    if (!allowed) return reply.code(400).send({ error: "host_not_allowed" });

    try {
      const res = await fetch(url, {
        headers: { Accept: "text/calendar, text/plain, */*" },
        redirect: "follow",
      });
      if (!res.ok) {
        return reply.code(502).send({ error: "fetch_failed", status: res.status });
      }
      const text = await res.text();
      if (!/BEGIN:VCALENDAR/i.test(text)) {
        return reply.code(422).send({ error: "not_ics" });
      }
      return { ok: true, text, bytes: text.length, fetchedAt: new Date().toISOString() };
    } catch (err) {
      app.log.error(err, "calendar-ics fetch failed");
      return reply.code(502).send({ error: "fetch_error" });
    }
  });
}
