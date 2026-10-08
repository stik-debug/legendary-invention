import os
from db import DB, init_db
import backup
import features
import notify

url=os.environ.get('DATABASE_URL') or 'sqlite:///instance/chamapay.db'
db=DB(url)
try:
    init_db(db)
    # 24-hour reminders are sent once per meeting; email delivery itself is retried from the outbox.
    print('meeting reminders:',features.meeting_reminders(db))
finally:
    db.close()
print('email outbox:',notify.process_email_outbox(url,25))
print('daily backup:',backup.save_backup(url))
