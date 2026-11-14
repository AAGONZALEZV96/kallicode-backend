import { DataSource, Repository } from "typeorm";
import { Notification } from "./entidades/Notificacion";
import { Supplier } from "./entidades/Proveedor";

export class ProveedoresRepo extends Repository<Supplier> {
  constructor(private readonly ds: DataSource) {
    super(Supplier, ds.createEntityManager());
  }

  async avisar(body: string) {
    return this.ds.getRepository(Notification).save({ body });
  }
}
