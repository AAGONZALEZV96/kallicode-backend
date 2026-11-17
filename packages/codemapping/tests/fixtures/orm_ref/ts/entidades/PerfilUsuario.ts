import { Column, Entity, PrimaryColumn } from "typeorm";

// Sin nombre explícito: TypeORM usa snake_case del nombre de la clase (user_profile).
@Entity()
export class UserProfile {
  @PrimaryColumn()
  userId!: number;

  @Column()
  bio!: string;
}
