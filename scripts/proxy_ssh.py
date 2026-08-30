#!/usr/bin/env python3
"""SSH ProxyCommand: 通过 HTTP CONNECT 代理转发 SSH"""
import socket, sys, threading

def main():
    host = sys.argv[1]
    port = int(sys.argv[2])
    
    proxy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    proxy.connect(('127.0.0.1', 18080))
    proxy.sendall(f'CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n'.encode())
    resp = b''
    while b'\r\n\r\n' not in resp:
        resp += proxy.recv(4096)
    if b'200' not in resp:
        sys.exit(1)
    
    def stdin_to_proxy():
        try:
            while True:
                data = sys.stdin.buffer.read(8192)
                if not data: break
                proxy.sendall(data)
        except: pass
        try: proxy.close()
        except: pass
    
    def proxy_to_stdout():
        try:
            while True:
                data = proxy.recv(8192)
                if not data: break
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
        except: pass
    
    t1 = threading.Thread(target=stdin_to_proxy, daemon=True)
    t2 = threading.Thread(target=proxy_to_stdout, daemon=True)
    t1.start(); t2.start()
    t1.join(); t2.join()

main()
