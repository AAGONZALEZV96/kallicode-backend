import { Column, Entity, PrimaryGeneratedColumn } from "typeorm";

@Entity({ name: "suppliers", schema: "inventory" })
export class Supplier {
  @PrimaryGeneratedColumn()
  id!: number;

  @Column()
  name!: string;
}
