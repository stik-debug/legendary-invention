/**
 * ChamaPay Database Layer — Node.js built-in node:sqlite (Node 22+).
 * Production path later: PostgreSQL. Local/deploy: SQLite file.
 * All money values are INTEGER (whole KES).
 */
import { DatabaseSync } from 'node:sqlite';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

let db: DatabaseSync | null = null;

export function getDb(): DatabaseSync {
  if (db) return db;
  const dbPath = process.env.DATABASE_PATH || path.join(process.cwd(), 'data', 'chamapay.db');
  const dir = path.dirname(dbPath);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  db = new DatabaseSync(dbPath);
  db.exec('PRAGMA journal_mode = WAL');
  db.exec('PRAGMA foreign_keys = ON');
  db.exec('PRAGMA busy_timeout = 5000');
  return db;
}

export function closeDb(): void {
  if (db) {
    db.close();
    db = null;
  }
}

function resolveSchemaPath(): string {
  // Works from src/db (tsx) and dist/db (compiled)
  const candidates = [
    path.join(__dirname, 'schema.sql'),
    path.join(process.cwd(), 'src', 'db', 'schema.sql'),
    path.join(process.cwd(), 'dist', 'db', 'schema.sql'),
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  throw new Error('schema.sql not found. Run postbuild or keep src/db/schema.sql.');
}

export function runMigration(): void {
  const database = getDb();
  const schema = fs.readFileSync(resolveSchemaPath(), 'utf-8');
  database.exec(schema);
  console.log('[db] Migration applied successfully');
}

/** Execute within a transaction. Rolls back on any error. */
export function withTransaction<T>(fn: (database: DatabaseSync) => T): T {
  const database = getDb();
  database.exec('BEGIN');
  try {
    const result = fn(database);
    database.exec('COMMIT');
    return result;
  } catch (e) {
    try {
      database.exec('ROLLBACK');
    } catch {
      /* ignore */
    }
    throw e;
  }
}

export function generateId(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

export function nowIso(): string {
  return new Date().toISOString().replace('T', ' ').substring(0, 19);
}
