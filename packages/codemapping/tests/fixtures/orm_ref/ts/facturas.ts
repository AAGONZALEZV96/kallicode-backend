import { PrismaService } from "./prisma.service";

export class FacturasService {
  constructor(private readonly prisma: PrismaService) {}

  async emitir(customerId: number, total: number) {
    await this.prisma.customer.upsert({
      where: { id: customerId },
      create: { id: customerId, name: "?" },
      update: {},
    });
    return this.prisma.invoice.create({ data: { total } });
  }

  async fantasmas() {
    // Trampa: modelo Prisma cuya tabla no existe en el esquema.
    return this.prisma.ghost.findMany();
  }
}
