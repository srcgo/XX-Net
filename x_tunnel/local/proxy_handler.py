import time
import socket, SocketServer, struct

from xlog import getLogger
xlog = getLogger("x_tunnel")

import global_var as g


class Socks5Server(SocketServer.StreamRequestHandler):
    read_buffer = ""
    buffer_start = 0

    def handle(self):
        try:
            # xlog.debug('Connected from %r', self.client_address)

            socks_version = ord(self.read_bytes(1))
            if socks_version == 4:
                self.socks4_handler()
            elif socks_version == 5:
                self.socks5_handler()
            else:
                xlog.warn("socks version:%d not supported", socks_version)
                return

        except socket.error as e:
            xlog.debug('socks handler read error %r', e)
        except Exception as e:
            xlog.exception("any err:%r", e)

    def read_line(self):
        sock = self.connection
        sock.setblocking(0)
        try:
            while True:
                n1 = self.read_buffer.find("\x00", self.buffer_start)
                if n1 > -1:
                    line = self.read_buffer[self.buffer_start:n1]
                    self.buffer_start = n1 + 1
                    return line
                time.sleep(0.001)
                data = sock.recv(256)
                self.read_buffer += data
        finally:
            sock.setblocking(1)

    def read_bytes(self, size):
        sock = self.connection
        sock.setblocking(1)
        try:
            while True:
                left = len(self.read_buffer) - self.buffer_start
                if left >= size:
                    break

                need = size - left
                data = sock.recv(need)
                if len(data):
                    self.read_buffer += data
                else:
                    raise socket.error("recv fail")
        finally:
            sock.setblocking(1)

        data = self.read_buffer[self.buffer_start:self.buffer_start + size]
        self.buffer_start += size
        return data

    def socks4_handler(self):
        # Socks4 or Socks4a
        sock = self.connection
        cmd = ord(self.read_bytes(1))
        if cmd != 1:
            xlog.warn("Socks4 cmd:%d not supported", cmd)
            return

        data = self.read_bytes(6)
        port = struct.unpack(">H", data[0:2])[0]
        addr_pack = data[2:6]
        if addr_pack[0:3] == '\x00\x00\x00' and addr_pack[3] != '\x00':
            domain_mode = True
        else:
            ip = socket.inet_ntoa(addr_pack)
            domain_mode = False

        user_id = self.read_line()
        if len(user_id):
            xlog.debug("Socks4 user_id:%s", user_id)

        if domain_mode:
            addr = self.read_line()
        else:
            addr = ip

        xlog.info("Socks4:%r to %s:%d", self.client_address, addr, port)
        conn_id = g.session.create_conn(sock, addr, port)
        if not conn_id:
            xlog.warn("Socks4 connect fail, no conn_id")
            reply = b"\x00\x5b\x00" + addr_pack + struct.pack(">H", port)
            sock.send(reply)
            return

        reply = b"\x00\x5a" + addr_pack + struct.pack(">H", port)
        sock.send(reply)

        if len(self.read_buffer) - self.buffer_start:
            g.session.conn_list[conn_id].transfer_received_data(self.read_buffer[self.buffer_start:])

        g.session.conn_list[conn_id].start(block=True)

    def socks5_handler(self):
        sock = self.connection
        auth_mode_num = ord(self.read_bytes(1))
        data = self.read_bytes(auth_mode_num)
        # xlog.debug("client version:%d, auth num:%d, list:%s", 5, auth_mode_num, utils.str2hex(data))

        sock.send(b"\x05\x00")  # socks version 5, no auth needed.

        data = self.read_bytes(4)
        socks_version = ord(data[0])
        if socks_version != 5:
            xlog.warn("request version:%d error", socks_version)
            return

        command = ord(data[1])
        if command != 1:  # 1. Tcp connect
            xlog.warn("request not supported command mode:%d", command)
            sock.send(b"\x05\x07\x00\x01")  # Command not supported
            return

        addrtype_pack = data[3]
        addrtype = ord(addrtype_pack)
        if addrtype == 1:  # IPv4
            addr_pack = self.read_bytes(4)
            addr = socket.inet_ntoa(addr_pack)
        elif addrtype == 3:  # Domain name
            domain_len_pack = self.read_bytes(1)[0]
            domain_len = ord(domain_len_pack)
            domain = self.read_bytes(domain_len)
            addr_pack = domain_len_pack + domain
            addr = domain
        elif addrtype == 4:  # IPv6
            addr_pack = self.read_bytes(16)
            addr = socket.inet_ntop(socket.AF_INET6, addr_pack)
        else:
            xlog.warn("request address type unknown:%d", addrtype)
            sock.send(b"\x05\x07\x00\x01")  # Command not supported
            return

        port = struct.unpack('>H', self.rfile.read(2))[0]

        # xlog.info("%r to %s:%d", self.client_address, addr, port)
        conn_id = g.session.create_conn(sock, addr, port)
        if not conn_id:
            reply = b"\x05\x01\x00" + addrtype_pack + addr_pack + struct.pack(">H", port)
            sock.send(reply)
            return

        reply = b"\x05\x00\x00" + addrtype_pack + addr_pack + struct.pack(">H", port)
        sock.send(reply)

        if len(self.read_buffer) - self.buffer_start:
            g.session.conn_list[conn_id].transfer_received_data(self.read_buffer[self.buffer_start:])

        g.session.conn_list[conn_id].start(block=True)
