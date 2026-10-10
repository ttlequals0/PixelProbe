from sqlalchemy import func, and_, or_

from pixelprobe.models import db, ScanResult


def _duplicate_groups(mode):
    path = ScanResult.file_path
    name = (func.split_part(path, '/', -1) if db.engine.dialect.name == 'postgresql'
            else func.substr(path, func.length(func.rtrim(path, func.replace(path, '/', ''))) + 1))
    keys = [ScanResult.file_hash, ScanResult.file_size] if mode == 'hash' else [name]
    eligible = [ScanResult.file_exists.is_(True)]
    if mode == 'hash':
        eligible.extend([
            ScanResult.file_hash.isnot(None),
            func.trim(ScanResult.file_hash) != '',
            ScanResult.file_size.isnot(None),
            ScanResult.bitrot_suspected.is_(False),
            or_(ScanResult.scan_status.is_(None),
                ScanResult.scan_status.notin_(['error', 'failed', 'unreadable'])),
            or_(ScanResult.scan_tool.is_(None), ScanResult.scan_tool != 'error'),
            or_(ScanResult.last_integrity_outcome.is_(None),
                ScanResult.last_integrity_outcome.notin_(['error', 'unreadable'])),
        ])
    else:
        eligible.append(name != '')
    groups = db.session.query(
        *(key.label(f'key_{index}') for index, key in enumerate(keys)),
        func.count().label('group_size'),
        func.min(ScanResult.id).label('group_id'),
    ).filter(*eligible).group_by(*keys).having(func.count() > 1).subquery()
    return groups, keys, eligible


def duplicate_members(mode):
    groups, keys, eligible = _duplicate_groups(mode)
    return db.session.query(
        ScanResult.id.label('file_id'), groups.c.group_size, groups.c.group_id,
    ).join(groups, and_(
        *(key == groups.c[f'key_{index}'] for index, key in enumerate(keys))
    )).filter(*eligible).subquery()


def duplicate_statistics():
    result = {}
    for mode, prefix in [('hash', 'duplicate'), ('name', 'filename_duplicate')]:
        groups, _, _ = _duplicate_groups(mode)
        files, group_count = db.session.query(
            func.coalesce(func.sum(groups.c.group_size), 0), func.count(),
        ).select_from(groups).one()
        files = int(files)
        result.update({
            f'{prefix}_files': files,
            f'{prefix}_groups': group_count,
            f'{prefix}_extra_files': files - group_count,
        })
    return result
