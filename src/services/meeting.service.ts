import { getDb, generateId, nowIso } from '../db/index.js';
import { ValidationError, NotFoundError } from '../utils/errors.js';

export function createMeeting(input: {
  chamaId: string;
  title: string;
  meetingDate: string;
  location?: string;
  agenda?: string;
  createdBy: string;
}) {
  if (!input.title?.trim()) throw new ValidationError('Title required');
  if (!input.meetingDate) throw new ValidationError('Date required');
  const id = generateId();
  const now = nowIso();
  getDb()
    .prepare(
      `INSERT INTO meetings (id, chama_id, title, meeting_date, location, agenda, created_by, is_test_data, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)`
    )
    .run(id, input.chamaId, input.title.trim(), input.meetingDate, input.location || null, input.agenda || null, input.createdBy, now);
  return { meetingId: id };
}

export function listMeetings(chamaId: string) {
  return getDb()
    .prepare(`SELECT * FROM meetings WHERE chama_id = ? ORDER BY meeting_date DESC`)
    .all(chamaId)
    .map((r: any) => ({
      id: String(r.id),
      title: String(r.title),
      meeting_date: String(r.meeting_date),
      location: r.location ? String(r.location) : null,
      agenda: r.agenda ? String(r.agenda) : null,
    }));
}

export function recordAttendance(input: {
  chamaId: string;
  meetingId: string;
  memberId: string;
  present: boolean;
  recordedBy: string;
}) {
  const db = getDb();
  const meeting = db.prepare(`SELECT id FROM meetings WHERE id = ? AND chama_id = ?`).get(input.meetingId, input.chamaId);
  if (!meeting) throw new NotFoundError('Meeting not found');
  const member = db
    .prepare(`SELECT id, user_id FROM chama_members WHERE id = ? AND chama_id = ?`)
    .get(input.memberId, input.chamaId) as any;
  if (!member) throw new NotFoundError('Member not found');
  const existing = db
    .prepare(`SELECT id FROM meeting_attendance WHERE meeting_id = ? AND member_id = ?`)
    .get(input.meetingId, input.memberId) as any;
  const now = nowIso();
  if (existing) {
    db.prepare(`UPDATE meeting_attendance SET present = ?, recorded_by = ? WHERE id = ?`).run(
      input.present ? 1 : 0,
      input.recordedBy,
      existing.id
    );
    return { attendanceId: String(existing.id) };
  }
  const id = generateId();
  db.prepare(
    `INSERT INTO meeting_attendance (id, meeting_id, chama_id, member_id, user_id, present, recorded_by, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)`
  ).run(id, input.meetingId, input.chamaId, member.id, member.user_id, input.present ? 1 : 0, input.recordedBy, now);
  return { attendanceId: id };
}
