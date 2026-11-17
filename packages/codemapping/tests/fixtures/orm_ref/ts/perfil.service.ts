import { DataSource } from "typeorm";
import { UserProfile } from "./entidades/PerfilUsuario";
import { Supplier } from "./entidades/Proveedor";
import { LegacyFlag } from "./entidades/Bandera";

export class PerfilService {
  constructor(private readonly dataSource: DataSource) {}

  async perfil(userId: number) {
    return this.dataSource.manager.findOneBy(UserProfile, { userId });
  }

  async proveedoresActivos() {
    return this.dataSource
      .createQueryBuilder(Supplier, "s")
      .where("s.active = :a", { a: true })
      .getMany();
  }

  async banderas() {
    // Trampa: entidad sin tabla en el esquema.
    return this.dataSource.manager.find(LegacyFlag);
  }
}
