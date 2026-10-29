/**
 * Smoke test: Auth → Create Chama → Add members up to Starter limit → Reject 16th
 */
import 'dotenv/config';
import { runMigration, getDb, closeDb, generateId, nowIso } from '../db/index.js';
import * as authService from '../services/auth.service.js';
import * as chamaService from '../services/chama.service.js';

async function main() {
  console.log('=== ChamaPay Smoke: Auth + Chama + Member Limit ===\n');
  runMigration();

  const db = getDb();
  const planCount = (db.prepare('SELECT COUNT(*) as c FROM subscription_plans').get() as any).c;
  if (planCount === 0) {
    const now = nowIso();
    for (const p of [
      { code: 'STARTER', name: 'Starter', price: 500, max: 15 },
      { code: 'GROWTH', name: 'Growth', price: 1500, max: 70 },
      { code: 'BUSINESS', name: 'Business', price: 2000, max: 100 },
    ]) {
      db.prepare(
        `INSERT INTO subscription_plans (id, code, name, price_kes, max_members, is_active, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, 1, ?, ?)`
      ).run(generateId(), p.code, p.name, p.price, p.max, now, now);
    }
    console.log('Plans seeded');
  }

  const admin = await authService.register({
    email: 'admin.umoja@example.test',
    phone: '254700000002',
    password: 'TestPass123!',
    fullName: 'Umoja Admin',
  });
  console.log('✓ AC-001 Register:', admin.userId);

  const login = await authService.login({
    emailOrPhone: 'admin.umoja@example.test',
    password: 'TestPass123!',
  });
  console.log('✓ AC-003 Login, token length:', login.token.length);

  const { chamaId } = chamaService.createChama({
    name: 'TEST-UMOJA',
    description: 'Smoke test chama',
    createdBy: admin.userId,
    planCode: 'STARTER',
  });
  console.log('✓ AC-010 Create Chama:', chamaId);

  const chama = chamaService.getChama(chamaId, admin.userId, false);
  console.log('  Plan:', chama.subscription.plan_code, 'Status:', chama.subscription.status, 'Members:', chama.memberCount);

  const extraIds: string[] = [];
  for (let i = 1; i <= 16; i++) {
    const u = await authService.register({
      email: `member${i}.umoja@example.test`,
      phone: `25470000${String(1000 + i).slice(1)}`,
      password: 'TestPass123!',
      fullName: `Member ${i}`,
    });
    extraIds.push(u.userId);
  }
  console.log('✓ Registered 16 extra users');

  for (let i = 0; i < 14; i++) {
    chamaService.addMember({
      chamaId,
      userId: extraIds[i],
      role: 'MEMBER',
      addedBy: admin.userId,
    });
  }
  const count15 = chamaService.getMemberCount(chamaId);
  console.log('✓ Starter 15 members allowed. Count:', count15);
  if (count15 !== 15) throw new Error('Expected 15 members');

  let rejected = false;
  try {
    chamaService.addMember({
      chamaId,
      userId: extraIds[14],
      role: 'MEMBER',
      addedBy: admin.userId,
    });
  } catch (e: any) {
    rejected = true;
    console.log('✓ AC-013 Starter 16th REJECTED:', e.message);
  }
  if (!rejected) throw new Error('Expected 16th member to be rejected');

  console.log('  Final member count still:', chamaService.getMemberCount(chamaId));

  const members = chamaService.listMembers(chamaId);
  const toRemove = members.find((m: any) => m.role === 'MEMBER');
  if (toRemove) {
    chamaService.removeMember({
      chamaId,
      memberId: String(toRemove.member_id),
      removedBy: admin.userId,
    });
    console.log('✓ AC-014 Soft-remove member (financial history preserved)');
    chamaService.addMember({
      chamaId,
      userId: extraIds[14],
      role: 'MEMBER',
      addedBy: admin.userId,
    });
    console.log('✓ After remove, new member allowed. Count:', chamaService.getMemberCount(chamaId));
  }

  console.log('\n=== SMOKE PASSED: Auth + Chama + Starter limit enforcement ===');
  closeDb();
}

main().catch((e) => {
  console.error('SMOKE FAILED:', e);
  process.exit(1);
});
