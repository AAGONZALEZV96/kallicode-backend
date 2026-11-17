import { Injectable } from "@nestjs/common";
import { InjectRepository } from "@nestjs/typeorm";
import { DataSource, Repository } from "typeorm";
import { Address } from "./entidades/Direccion";
import { Notification } from "./entidades/Notificacion";

@Injectable()
export class NotificacionesService {
  constructor(
    @InjectRepository(Notification) private readonly repo: Repository<Notification>,
    private readonly dataSource: DataSource,
  ) {}

  async direccionesDe(customerId: number) {
    return this.dataSource.getRepository(Address).find({ where: { customerId } });
  }

  async pendientes() {
    return this.repo.find({ where: { leida: false } });
  }
}
