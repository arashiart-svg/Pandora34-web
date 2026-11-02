import Fastify from "fastify";
import cors from "@fastify/cors";
import { env } from "./env";
import { prisma } from "./prisma";
import { hashPassword } from "./auth/password";
import { healthRoutes } from "./routes/health";
import { authRoutes } from "./routes/auth";
import { syncRoutes } from "./routes/sync";

/** Создаёт первого администратора из ADMIN_USERNAME/ADMIN_PASSWORD, если пользователей ещё нет. */
async function bootstrapAdmin(): Promise<void> {
  const usersCount = await prisma.user.count();
  if (usersCount > 0) return;
  if (!env.adminPassword) {
    console.warn(
      "Пользователей нет, но ADMIN_PASSWORD не задан — первый администратор не создан. Задайте ADMIN_PASSWORD в .env и перезапустите."
    );
    return;
  }
  await prisma.user.create({
    data: {
      username: env.adminUsername,
      passwordHash: await hashPassword(env.adminPassword),
      name: env.adminUsername,
      role: "OWNER",
      masterName: env.ownerMasterName,
    },
  });
  console.log(`Создан первый администратор: ${env.adminUsername}`);
}

async function main(): Promise<void> {
  const app = Fastify({ logger: true, bodyLimit: 20 * 1024 * 1024 });

  app.addContentTypeParser("application/x-www-form-urlencoded", { parseAs: "string" }, (_req, body, done) => {
    try {
      done(null, Object.fromEntries(new URLSearchParams(String(body))));
    } catch (err) {
      done(err as Error);
    }
  });

  await app.register(cors, { origin: true });

  await app.register(healthRoutes);
  await app.register(authRoutes);
  await app.register(syncRoutes);

  await bootstrapAdmin();

  await app.listen({ port: env.port, host: "0.0.0.0" });
}

main().catch((err) => {
  console.error("Не удалось запустить API", err);
  process.exit(1);
});
