import "dotenv/config";

function required(name: string, fallback?: string): string {
  const value = process.env[name] ?? fallback;
  if (value === undefined || value === "") {
    throw new Error(`Отсутствует обязательная переменная окружения: ${name}`);
  }
  return value;
}

export const env = {
  nodeEnv: process.env.NODE_ENV ?? "development",
  port: Number(process.env.API_PORT ?? 4000),
  databaseUrl: required("DATABASE_URL"),
  jwtSecret: required("JWT_SECRET"),
  syncToken: required("SYNC_TOKEN"),
  botInternalToken: required("BOT_INTERNAL_TOKEN"),
  ownerMasterName: process.env.OWNER_MASTER_NAME ?? "Андрей",
  adminUsername: process.env.ADMIN_USERNAME ?? "admin",
  adminPassword: process.env.ADMIN_PASSWORD ?? "",
  webGateUser: (process.env.WEB_GATE_USER ?? process.env.ADMIN_USERNAME ?? "admin").trim(),
  webGatePassword: (process.env.WEB_GATE_PASSWORD ?? process.env.ADMIN_PASSWORD ?? "").trim(),
  tgStaffBotToken: process.env.TG_STAFF_BOT_TOKEN ?? "",
  tgStaffChatIds: (process.env.TG_STAFF_CHAT_IDS ?? "")
    .split(",")
    .map((id) => id.trim())
    .filter(Boolean),
  tgClientBotToken: process.env.TG_CLIENT_BOT_TOKEN ?? "",
  icsFetchProxy: (process.env.ICS_FETCH_PROXY ?? "").trim(),
  icsProxySecret: (process.env.ICS_PROXY_SECRET ?? "").trim(),
  redisUrl: (process.env.REDIS_URL ?? "").trim(),
};
