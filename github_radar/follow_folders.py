"""Logical folders: classification never creates or removes a follow."""
import sqlite3
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class FollowFolder:
    id: int
    name: str
    count: int


def positive_id(value):
    if type(value) is not int or not 0<value<=9223372036854775807:
        raise ValueError('分类ID无效')
    return value


def name_key(name):
    if not isinstance(name,str):raise ValueError('请输入文件夹名称')
    name=name.strip()
    if not 1<=len(name)<=40 or any(unicodedata.category(c) in ('Cc','Cf','Cs') for c in name):
        raise ValueError('文件夹名称应为1～40个字符，不能包含控制字符')
    return name,unicodedata.normalize('NFKC',name).casefold()


def list_folders(db):
    rows=db.execute('''SELECT f.id,f.name,COUNT(i.repo_id) AS item_count FROM follow_folders f
        LEFT JOIN follow_folder_items i ON i.folder_id=f.id GROUP BY f.id ORDER BY f.sort_position,f.id''')
    return [FollowFolder(r['id'],r['name'],r['item_count']) for r in rows]


def _folder(db,identity):
    positive_id(identity)
    row=db.execute('SELECT id FROM follow_folders WHERE id=?',(identity,)).fetchone()
    if row is None:raise LookupError('文件夹不存在')


def create_folder(db,name,at):
    name,key=name_key(name)
    if db.execute('SELECT COUNT(*) FROM follow_folders').fetchone()[0]>=200:
        raise ValueError('最多创建200个文件夹')
    # Persist the high-water mark independently of deleted rows. BEGIN IMMEDIATE
    # in the store serializes allocation and insertion, including legacy migration.
    last=db.execute('SELECT last_id FROM follow_folder_sequence WHERE singleton=1').fetchone()[0]
    if last>=2**63-1:raise ValueError('分类ID已用尽')
    identity=last+1
    position=db.execute('SELECT COALESCE(MAX(sort_position),-1)+1 FROM follow_folders').fetchone()[0]
    try:db.execute('INSERT INTO follow_folders(id,name,name_key,created_at,sort_position) VALUES (?,?,?,?,?)',(identity,name,key,at,position))
    except sqlite3.IntegrityError:raise ValueError('已存在同名文件夹') from None
    db.execute('UPDATE follow_folder_sequence SET last_id=? WHERE singleton=1',(identity,))
    return FollowFolder(identity,name,0)


def rename_folder(db,identity,name):
    _folder(db,identity);name,key=name_key(name)
    try:db.execute('UPDATE follow_folders SET name=?,name_key=? WHERE id=?',(name,key,identity))
    except sqlite3.IntegrityError:raise ValueError('已存在同名文件夹') from None
    count=db.execute('SELECT COUNT(*) FROM follow_folder_items WHERE folder_id=?',(identity,)).fetchone()[0]
    return FollowFolder(identity,name,count)


def delete_folder(db,identity):
    _folder(db,identity)
    db.execute('DELETE FROM follow_folders WHERE id=?',(identity,))


def reorder_folders(db,ids):
    if not isinstance(ids,list) or len(ids)>200:raise ValueError('分类排序无效')
    for identity in ids:positive_id(identity)
    if len(set(ids))!=len(ids):raise ValueError('分类ID不能重复')
    current={row[0] for row in db.execute('SELECT id FROM follow_folders')}
    if set(ids)!=current:raise ValueError('分类已变化，请刷新后重试')
    db.executemany('UPDATE follow_folders SET sort_position=? WHERE id=?',enumerate(ids))


def move_unfiled(db,repo_id,folder_id):
    _follow(db,repo_id);_folder(db,folder_id)
    if db.execute('SELECT 1 FROM follow_folder_items WHERE repo_id=?',(repo_id,)).fetchone():
        raise ValueError('项目已分类，请刷新后重试')
    db.execute('INSERT INTO follow_folder_items(folder_id,repo_id) VALUES (?,?)',(folder_id,repo_id))


def _follow(db,repo_id):
    positive_id(repo_id)
    if db.execute('SELECT 1 FROM follows WHERE repo_id=?',(repo_id,)).fetchone() is None:
        raise LookupError('项目尚未关注')


def folder_ids(db,repo_id):
    _follow(db,repo_id)
    return [r[0] for r in db.execute('SELECT folder_id FROM follow_folder_items WHERE repo_id=? ORDER BY folder_id',(repo_id,))]


def set_folders(db,repo_id,ids):
    _follow(db,repo_id)
    if not isinstance(ids,(list,tuple)) or len(ids)>200:raise ValueError('分类列表无效')
    for identity in ids:positive_id(identity)
    if len(set(ids))!=len(ids):raise ValueError('分类ID不能重复')
    for identity in ids:_folder(db,identity)
    db.execute('DELETE FROM follow_folder_items WHERE repo_id=?',(repo_id,))
    db.executemany('INSERT INTO follow_folder_items(folder_id,repo_id) VALUES (?,?)',((i,repo_id) for i in ids))


def following_filter(db,folder):
    if folder=='all':return '',()
    if folder=='unfiled':return ' WHERE NOT EXISTS (SELECT 1 FROM follow_folder_items i WHERE i.repo_id=f.repo_id)',()
    if not isinstance(folder,str) or not folder.isascii() or not folder.isdecimal():raise ValueError('关注分类无效')
    identity=positive_id(int(folder));_folder(db,identity)
    return ' WHERE EXISTS (SELECT 1 FROM follow_folder_items i WHERE i.repo_id=f.repo_id AND i.folder_id=?)',(identity,)


def move_project(db,repo_id,source,target):
    """Move from one real folder, preserving memberships in other folders."""
    _follow(db,repo_id)
    current=folder_ids(db,repo_id)
    def parse(value):
        if value=='unfiled':return None
        if not isinstance(value,str) or not value.isascii() or not value.isdecimal():
            raise ValueError('分类移动位置无效')
        identity=positive_id(int(value));_folder(db,identity);return identity
    origin,destination=parse(source),parse(target)
    if (origin is None and current) or (origin is not None and origin not in current):
        raise ValueError('项目分类已变化，请刷新后重试')
    if origin==destination:return
    if destination is None:
        db.execute('DELETE FROM follow_folder_items WHERE repo_id=?',(repo_id,))
    else:
        if origin is not None:db.execute('DELETE FROM follow_folder_items WHERE repo_id=? AND folder_id=?',(repo_id,origin))
        db.execute('INSERT OR IGNORE INTO follow_folder_items VALUES(?,?)',(destination,repo_id))

def classify_project(db,repo_id,ids,create_name,at):
    """Validate, create, follow and classify in the caller's single transaction."""
    positive_id(repo_id)
    if db.execute('SELECT 1 FROM repositories WHERE id=?',(repo_id,)).fetchone() is None:
        raise LookupError('项目不存在')
    if not isinstance(ids,list) or len(ids)>200:raise ValueError('分类列表无效')
    for identity in ids:_folder(db,identity)
    if len(set(ids))!=len(ids):raise ValueError('分类ID不能重复')
    if not ids and create_name is None and db.execute('SELECT 1 FROM follows WHERE repo_id=?',(repo_id,)).fetchone() is None:
        raise ValueError('请选择或创建文件夹')
    selected=list(ids)
    if create_name is not None:selected.append(create_folder(db,create_name,at).id)
    db.execute('INSERT OR IGNORE INTO follows VALUES(?,?)',(repo_id,at))
    set_folders(db,repo_id,selected)
    return {'followed':True,'ids':selected,
            'folders':[{'id':f.id,'name':f.name,'count':f.count} for f in list_folders(db)]}
