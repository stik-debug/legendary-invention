"""Durable logical backup. Uploads a compressed JSON export to S3-compatible object storage when configured."""
import gzip, json, os
from datetime import datetime, timezone
from db import DB

# Password hashes are required for a faithful restore; they are not plaintext passwords.
# Keep backups in private, encrypted storage. One-time tokens and MFA secrets remain excluded.
SENSITIVE={'claim_code_hash','reset_hash','totp_secret','totp_recovery','code_hash'}

def _tables(db):
    if db.pg:
        return [r['table_name'] for r in db.all("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() AND table_type='BASE TABLE' ORDER BY table_name")]
    return [r['name'] for r in db.all("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]

def export_database(database_url):
    db=DB(database_url)
    try:
        payload={'format':'chamapay-daily-backup-v2','exported_at':datetime.now(timezone.utc).replace(microsecond=0).isoformat(sep=' '),'tables':{}}
        for table in _tables(db):
            rows=db.all('SELECT * FROM '+table)
            safe=[]
            for row in rows:
                safe.append({k:v for k,v in row.items() if k not in SENSITIVE})
            payload['tables'][table]=safe
        return gzip.compress(json.dumps(payload,default=str,separators=(',',':')).encode('utf-8'),compresslevel=6)
    finally: db.close()

def save_backup(database_url):
    bucket=os.environ.get('BACKUP_S3_BUCKET')
    # Render cron containers are ephemeral. Do not report a successful "daily backup"
    # when the only copy would be written to /tmp and disappear after the job/container.
    if os.environ.get('RENDER') and not bucket:
        raise RuntimeError('Durable backup is not configured: set BACKUP_S3_BUCKET and S3 credentials for the Render cron job.')
    data=export_database(database_url)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    name=f'chamapay-{stamp}.json.gz'
    if bucket:
        import boto3
        client=boto3.client('s3',region_name=os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION'),endpoint_url=os.environ.get('S3_ENDPOINT_URL') or None,
                            aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'))
        key=(os.environ.get('BACKUP_S3_PREFIX') or 'chamapay').strip('/')+'/'+name
        client.put_object(Bucket=bucket,Key=key,Body=data,ContentType='application/gzip',ServerSideEncryption=os.environ.get('BACKUP_S3_SSE','AES256'))
        return f's3://{bucket}/{key}'
    directory=os.environ.get('BACKUP_DIR') or '/tmp/chamapay-backups'
    os.makedirs(directory,exist_ok=True)
    path=os.path.join(directory,name)
    with open(path,'wb') as f: f.write(data)
    return path


def validate_backup(data):
    """Validate a logical backup without modifying any database."""
    try:
        payload=json.loads(gzip.decompress(data).decode('utf-8'))
    except Exception as exc:
        raise ValueError('Backup is not a readable gzip JSON file.') from exc
    if payload.get('format') not in ('chamapay-daily-backup-v2',):
        raise ValueError('Unsupported backup format.')
    tables=payload.get('tables')
    if not isinstance(tables,dict) or not tables:
        raise ValueError('Backup contains no tables.')
    total=0
    for name,rows in tables.items():
        if not isinstance(name,str) or not name.replace('_','').isalnum() or not isinstance(rows,list):
            raise ValueError('Backup table structure is invalid.')
        if any(not isinstance(row,dict) for row in rows):
            raise ValueError(f'Invalid row structure in {name}.')
        total += len(rows)
    return {'payload':payload,'table_count':len(tables),'row_count':total}

def restore_backup_to_empty_sqlite(data, target_path):
    """Restore a backup into a NEW/EMPTY SQLite staging database for recovery drills only. Never targets production."""
    import os
    from db import init_db
    if target_path in (':memory:', ''):
        raise ValueError('Use a named staging database file so the result can be inspected.')
    if os.path.exists(target_path) and os.path.getsize(target_path)>0:
        raise ValueError('Refusing to overwrite an existing database. Choose a new staging path.')
    checked=validate_backup(data); payload=checked['payload']
    target=DB('sqlite:///'+target_path)
    try:
        init_db(target)
        # SCHEMA order follows dependency order (users before chamas, chamas before their records).
        from db import SCHEMA
        ordered=[]
        import re
        for match in re.finditer(r'CREATE TABLE IF NOT EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)',SCHEMA,re.I):
            name=match.group(1)
            if name in payload['tables'] and name not in ordered: ordered.append(name)
        ordered += [name for name in payload['tables'] if name not in ordered]
        with target.tx():
            # init_db may seed default plans/settings; clear the staging-only defaults before restore.
            for table in reversed(ordered):
                target.execute('DELETE FROM '+table)
            for table in ordered:
                rows=payload['tables'][table]
                if not rows: continue
                # Verify table and columns against the destination schema before inserting.
                if target.pg: raise ValueError('Restore drill only supports SQLite staging databases.')
                cols={r['name'] for r in target.all('PRAGMA table_info('+table+')')}
                for row in rows:
                    clean={k:v for k,v in row.items() if k in cols}
                    if not clean: continue
                    keys=list(clean)
                    sql='INSERT INTO '+table+' ('+','.join(keys)+') VALUES ('+','.join('?' for _ in keys)+')'
                    target.execute(sql,[clean[k] for k in keys])
        # Verify row counts and foreign-key consistency after the restore.
        for table,rows in payload['tables'].items():
            got=int(target.val('SELECT COUNT(*) FROM '+table,(),0) or 0)
            if got != len(rows): raise ValueError(f'Restored row count mismatch for {table}: expected {len(rows)}, got {got}.')
        fk=target.all('PRAGMA foreign_key_check')
        if fk: raise ValueError(f'Restored database has {len(fk)} foreign-key violation(s).')
        return {'table_count':checked['table_count'],'row_count':checked['row_count'],'foreign_key_violations':0}
    finally:
        target.close()
