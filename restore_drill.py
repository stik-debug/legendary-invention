"""Validate and restore a ChamaPay logical backup into a new SQLite staging file.
Usage: python restore_drill.py /path/to/backup.json.gz /tmp/chamapay-restore-drill.sqlite
Never point this script at production data or an existing database.
"""
import sys
from backup import validate_backup, restore_backup_to_empty_sqlite

if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    with open(sys.argv[1], 'rb') as f:
        data = f.read()
    checked = validate_backup(data)
    print(f"Backup readable: {checked['table_count']} tables, {checked['row_count']} rows")
    result = restore_backup_to_empty_sqlite(data, sys.argv[2])
    print(f"Restore drill passed: {result['table_count']} tables, {result['row_count']} rows, {result['foreign_key_violations']} FK violations")
