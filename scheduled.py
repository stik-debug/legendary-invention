import os
from db import DB, init_db
import backup
import features
import notify
from trust_jobs import daily_obligation_reminders

url=os.environ.get('DATABASE_URL') or 'sqlite:///instance/chamapay.db'
db=DB(url)
try:
    init_db(db)
    # Idempotent daily follow-ups plus 24-hour meeting reminders.
    print('obligation reminders:',daily_obligation_reminders(db))
    print('meeting reminders:',features.meeting_reminders(db))
    import services
    print('subscription nudges:',services.subscription_nudges(db))
finally:
    db.close()
print('email outbox:',notify.process_email_outbox(url,25))
print('WhatsApp outbox:',notify.process_whatsapp_outbox(url,25))
print('daily backup:',backup.save_backup(url))
