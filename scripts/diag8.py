# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, '/opt/kuaixuan/backend')
from app.services import security
# 用 admin shiren(id=6) 签发 token
tok = security.issue_token(6, remember=True)
print(tok)