import { Repository } from "./repository";
import type { Logger } from "../shared/logger";

export interface User {
  id: string;
  email: string;
}

export enum Role {
  Admin = "admin",
  Member = "member",
}

export type UserId = User["id"];

export namespace Legacy {
  export class Adapter {
    convert(raw: unknown): User {
      return raw as User;
    }
  }
}

export class UserService {
  constructor(private readonly repo: Repository<User>, private readonly log: Logger) {}

  async findActive(): Promise<User[]> {
    const all = await this.repo.findAll();
    return all.filter((u) => u.email.endsWith("@acme.cl"));
  }

  rename(id: UserId, email: string): void {
    this.repo.update(id, { email });
    this.log.info("renamed", { id });
  }
}

export function buildCache<T>(ttlMs: number) {
  class LocalCache {
    private store = new Map<string, T>();
    get(key: string): T | undefined {
      return this.store.get(key);
    }
  }
  return new LocalCache();
}

export const normalizeEmail = (email: string): string => email.trim().toLowerCase();

export const onRetry = function (attempt: number): number {
  return Math.min(1000 * 2 ** attempt, 30000);
};

export default function (): string {
  return "user-service";
}
