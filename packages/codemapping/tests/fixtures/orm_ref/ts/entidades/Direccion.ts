import { Column, Entity, ManyToOne, PrimaryGeneratedColumn } from "typeorm";
import { Notification } from "./Notificacion";

@Entity("addresses", { schema: "app" })
export class Address {
  @PrimaryGeneratedColumn()
  id!: number;

  @Column()
  street!: string;

  // Una relación no es un acceso del archivo a la tabla relacionada.
  @ManyToOne(() => Notification)
  ultimaNotificacion!: Notification;
}
