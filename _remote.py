import socket, sys, paramiko
HOST, PORT = "47.99.153.123", 22
PROXY = ("127.0.0.1", 18080)
USER, PASS = "root", "Admin@123."
cmd = " ".join(sys.argv[1:])
sock = socket.create_connection(PROXY, timeout=25)
req = "CONNECT %s:%s HTTP/1.1\r\nHost: %s:%s\r\n\r\n" % (HOST, PORT, HOST, PORT)
sock.sendall(req.encode())
buf = b""
while b"\r\n\r\n" not in buf:
    c = sock.recv(4096)
    if not c: break
    buf += c
    if len(buf) > 65536: break
if b"200" not in buf.split(b"\r\n", 1)[0]: raise RuntimeError("CONNECT failed")
cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, PORT, username=USER, password=PASS, sock=sock, timeout=30)
_, out, err = cli.exec_command(cmd, timeout=300)
o = out.read().decode(errors="replace")
e = err.read().decode(errors="replace")
if o: print(o)
if e: print("[stderr]", e)
cli.close()
