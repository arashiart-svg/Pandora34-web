import { FastifyInstance } from "fastify";
import { z } from "zod";
import { prisma } from "../prisma";
import { hashPassword, verifyPassword } from "../auth/password";
import { signUserToken } from "../auth/jwt";
import { loginBlocked, loginFailed, loginIp, loginSucceeded } from "../auth/login-guard";
import { requireRole, requireUser } from "../auth/middleware";

const loginSchema = z.object({
  username: z.string().min(1),
  password: z.string().min(1),
});

const createUserSchema = z.object({
  username: z.string().min(3),
  password: z.string().min(6),
  name: z.string().min(1),
  role: z.enum(["OWNER", "ADMIN", "MASTER"]).default("MASTER"),
  masterName: z.string().optional(),
});

export async function authRoutes(app: FastifyInstance): Promise<void> {
  app.post("/auth/login", async (request, reply) => {
    const parsed = loginSchema.safeParse(request.body);
    if (!parsed.success) {
      return reply.code(400).send({ error: "invalid_body", details: parsed.error.flatten() });
    }
    const { username, password } = parsed.data;
    const ip = loginIp(request);
    if (loginBlocked(ip, username)) {
      return reply.code(429).send({ error: "too_many" });
    }
    const user = await prisma.user.findUnique({ where: { username } });
    if (!user || !(await verifyPassword(password, user.passwordHash))) {
      loginFailed(ip, username);
      return reply.code(401).send({ error: "invalid_credentials" });
    }
    loginSucceeded(ip, username);
    const token = signUserToken({
      sub: user.id,
      username: user.username,
      role: user.role,
      masterName: user.masterName,
    });
    return { token, user: { id: user.id, username: user.username, name: user.name, role: user.role } };
  });

  app.get("/auth/me", { preHandler: requireUser }, async (request) => {
    return { user: request.user };
  });

  // Только владелец/админ может создавать новых сотрудников (для входа в будущую веб-панель
  // и для привязки Telegram-бота сотрудников через /auth/me).
  app.post(
    "/auth/users",
    { preHandler: [requireUser, requireRole("OWNER", "ADMIN")] },
    async (request, reply) => {
      const parsed = createUserSchema.safeParse(request.body);
      if (!parsed.success) {
        return reply.code(400).send({ error: "invalid_body", details: parsed.error.flatten() });
      }
      const { username, password, name, role, masterName } = parsed.data;
      const existing = await prisma.user.findUnique({ where: { username } });
      if (existing) {
        return reply.code(409).send({ error: "username_taken" });
      }
      const user = await prisma.user.create({
        data: { username, name, role, masterName, passwordHash: await hashPassword(password) },
      });
      return reply.code(201).send({ id: user.id, username: user.username, name: user.name, role: user.role });
    }
  );
}
