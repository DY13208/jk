"""Read-only capacity reporting. No cleanup or retention changes."""
import shutil
import time
from datetime import datetime, timezone


def storage_status(db):
    def size(path):
        try:
            return path.stat().st_size if path.is_file() and not path.is_symlink() else 0
        except FileNotFoundError:
            return 0

    database_bytes = size(db.path)
    wal_bytes = size(db.path.with_name(db.path.name + '-wal'))
    backups = []
    folder = db.path.parent / 'backups'
    for path in folder.glob('*.db'):
        if path.is_symlink() or not path.is_file(): continue
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        backups.append({'bytes':stat.st_size, 'modified':stat.st_mtime,
                        'kind':'daily' if path.name.startswith('daily-') else 'other'})
    disk = shutil.disk_usage(db.path.parent)
    warnings = []
    if disk.free < 2 * 1024**3 or disk.free / max(disk.total, 1) < .1:
        warnings.append('数据目录所在文件系统可用空间不足（低于 2 GiB 或 10%），请检查磁盘容量')
    if database_bytes > 1024**3:
        warnings.append('数据库超过 1 GiB，建议评估历史归档并验证备份恢复')
    if backups and max(row['modified'] for row in backups) < datetime.now(timezone.utc).timestamp() - 2*86400:
        warnings.append('最近两天未生成数据库备份，请检查备份任务')
    if not backups:
        warnings.append('尚无数据库备份，请先创建备份')
    with db.connect() as conn:
        samples = [dict(row) for row in conn.execute('SELECT * FROM monitor_storage_samples ORDER BY hour')]
    daily_growth = None
    if len(samples)>1 and samples[-1]['hour']-samples[0]['hour']>=24:
        first, last = samples[0], samples[-1]
        daily_growth = round(((last['database_bytes']+last['wal_bytes']+last['backup_bytes']) -
                              (first['database_bytes']+first['wal_bytes']+first['backup_bytes'])) *
                             24 / (last['hour']-first['hour']))
    forecast = round(disk.free/daily_growth, 1) if daily_growth and daily_growth>0 else None
    return {'database_bytes':database_bytes, 'wal_bytes':wal_bytes,
            'backup_bytes':sum(row['bytes'] for row in backups), 'backup_count':len(backups),
            'daily_backup_count':sum(row['kind']=='daily' for row in backups),
            'other_backup_count':sum(row['kind']=='other' for row in backups),
            'latest_backup_at':datetime.fromtimestamp(max(row['modified'] for row in backups), timezone.utc).isoformat() if backups else None,
            'disk':{'total_bytes':disk.total, 'free_bytes':disk.free},
            'growth':{'sample_count':len(samples),'bytes_per_day':daily_growth,'estimated_days_remaining':forecast,
                      'basis':'最近最多 90 天数据库、WAL 和备份的净增长估计，不含其他程序占用变化'},
            'warnings':warnings, 'retention':{'daily_keep':30, 'automatic_history_cleanup':False}}


def sample_storage(db):
    report = storage_status(db)
    hour = int(time.time()//3600)
    with db.connect() as conn:
        conn.execute('INSERT OR IGNORE INTO monitor_storage_samples VALUES(?,?,?,?,?)',
                     (hour,report['database_bytes'],report['wal_bytes'],report['backup_bytes'],report['disk']['free_bytes']))
        # Only capacity telemetry expires; business history and backups are never deleted here.
        conn.execute('DELETE FROM monitor_storage_samples WHERE hour<?', (hour-90*24,))
