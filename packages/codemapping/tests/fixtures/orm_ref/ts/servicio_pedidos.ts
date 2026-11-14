import { PrismaClient } from "@prisma/client";

const prisma = new PrismaClient();

export async function perfil(id: number) {
  return prisma.user.findUnique({ where: { id }, include: { orders: true } });
}

export async function pedidosDe(userId: number) {
  return prisma.order.findMany({ where: { userId } });
}

export async function renombrar(id: number, name: string) {
  return prisma.$transaction(async (tx) => {
    return tx.product.update({ where: { id }, data: { name } });
  });
}
