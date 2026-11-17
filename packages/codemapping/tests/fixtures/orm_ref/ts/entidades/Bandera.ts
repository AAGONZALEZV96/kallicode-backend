import { Entity, PrimaryColumn } from "typeorm";

// Trampa: entidad cuya tabla no existe en la base del cliente.
@Entity("legacy_flags")
export class LegacyFlag {
  @PrimaryColumn()
  code!: string;
}
