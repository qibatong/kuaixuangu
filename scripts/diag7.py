# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, '/opt/kuaixuan/backend')
from app.db import database
conn = database.get_conn()
rows = conn.execute("SELECT id, username, email, is_admin, member_level FROM users WHERE is_admin=1 OR username LIKE '%admin%' LIMIT 5").fetchall()
for r in rows:
    print(' ', dict(id=r[0], username=r[1], email=r[2], is_admin=r[3], member_level=r[4]))
conn.close()