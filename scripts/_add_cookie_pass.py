import io
p = "/etc/nginx/conf.d/kuaixuan.conf"
with io.open(p, "r", encoding="utf-8") as f:
    src = f.read()

# 找到 /__aipick_auth location, 在 proxy_set_header Host 之前插入 Cookie 透传
target = "        proxy_set_header Host $host;"
addition = "        proxy_set_header Cookie $http_cookie;\n"

if "Cookie $http_cookie" in src:
    print("ALREADY_HAS_COOKIE_LINE")
else:
    if target in src:
        with io.open(p + ".bak", "w", encoding="utf-8") as f:
            f.write(src)
        src = src.replace(target, addition + target, 1)
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(src)
        print("INSERTED")
    else:
        print("ANCHOR_NOT_FOUND")
