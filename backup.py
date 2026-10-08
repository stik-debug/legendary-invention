"""Durable logical backup. Uploads a compressed JSON export to S3-compatible object storage when configured."""
import gzip, json, os
from datetime import datetime
from db import DB

SENSITIVE={'password_hash','claim_code_hash','reset_hash','totp_secret','totp_recovery','code_hash'}

def _tables(db):
    if db.pg:
        return [r['table_name'] for r in db.all("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() AND table_type='BASE TABLE' ORDER BY table_name")]
    return [r['name'] for r in db.all("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]

def export_database(database_url):
    db=DB(database_url)
    try:
        payload={'format':'chamapay-daily-backup-v2','exported_at':datetime.utcnow().replace(microsecond=0).isoformat(sep=' '),'tables':{}}
        for table in _tables(db):
            rows=db.all('SELECT * FROM '+table)
            safe=[]
            for row in rows:
                safe.append({k:v for k,v in row.items() if k not in SENSITIVE})
            payload['tables'][table]=safe
        return gzip.compress(json.dumps(payload,default=str,separators=(',',':')).encode('utf-8'),compresslevel=6)
    finally: db.close()

def save_backup(database_url):
    data=export_database(database_url)
    stamp=datetime.utcnow().strftime('%Y%m%d-%H%M%S')
    name=f'chamapay-{stamp}.json.gz'
    bucket=os.environ.get('BACKUP_S3_BUCKET')
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
