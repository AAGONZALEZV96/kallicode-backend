import { Column, Entity, PrimaryGeneratedColumn } from "typeorm";

@Entity({ name: "notifications", schema: "app" })
export class Notification {
  @PrimaryGeneratedColumn()
  id!: number;

  @Column()
  body!: string;
}
