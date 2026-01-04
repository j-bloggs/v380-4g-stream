"""
V380 Cloud Client

Core client for connecting to V380 cloud servers.
Handles authentication, session management, and JSON-RPC communication.
"""

import socket
import struct
import json
import secrets
import time
from typing import Optional
from Crypto.Cipher import AES

from .crypto import generate_aes_key, generate_random_key, encrypt_password

# Default server configuration
DEFAULT_API_SERVER = "194.195.251.29"
DEFAULT_API_PORT = 8089
DEFAULT_REGISTER_PORT = 8900
DEFAULT_STREAM_PORT = 8800


class V380Client:
    """
    V380 Cloud Camera Client

    Handles connection to V380 cloud servers and authentication.
    Can be extended for streaming, playback, and control features.
    """

    def __init__(self, device_id: int, password: str,
                 server: str = DEFAULT_API_SERVER,
                 api_port: int = DEFAULT_API_PORT,
                 register_port: int = DEFAULT_REGISTER_PORT,
                 stream_port: int = DEFAULT_STREAM_PORT,
                 debug: bool = False):
        self.device_id = device_id
        self.password = password
        self.server = server
        self.api_port = api_port
        self.register_port = register_port
        self.stream_port = stream_port
        self.debug = debug

        # Session state
        self.session: Optional[int] = None
        self.handle: Optional[int] = None
        self.aes_key: Optional[bytes] = None
        self.cipher: Optional[AES] = None
        self.socket: Optional[socket.socket] = None

        # Camera capabilities
        self.audio_supported = False
        self.battery_level: Optional[int] = None

    @property
    def domain(self) -> str:
        """Camera domain name"""
        return f"{self.device_id}.nvdvr.net"

    @property
    def is_connected(self) -> bool:
        """Check if connected and authenticated"""
        return self.session is not None and self.handle is not None

    def connect(self) -> bool:
        """Connect to API server"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(15)
            self.socket.connect((self.server, self.api_port))
            print(f"[+] Connected to API server {self.server}:{self.api_port}")
            return True
        except Exception as e:
            print(f"[!] Connection failed: {e}")
            return False

    def disconnect(self):
        """Disconnect from server"""
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            self.socket = None
        self.session = None
        self.handle = None

    def register(self) -> bool:
        """
        Register device with cloud routing server.

        This prepares the cloud infrastructure to route streams for this device.
        Should be called before connect()/login(), especially for idle 4G cameras.
        """
        reg_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        reg_sock.settimeout(10)

        try:
            reg_sock.connect((self.server, self.register_port))
            print(f"[+] Connected to register server {self.server}:{self.register_port}")

            domain = self.domain.encode().ljust(48, b'\x00')
            packet = struct.pack('<II', 0x00ac, 0x03f4) + domain
            packet += struct.pack('<I', self.stream_port)
            packet += struct.pack('<I', self.device_id)
            packet = packet.ljust(64, b'\x00')

            reg_sock.sendall(packet)
            print("[*] Sent register request...")

            response = reg_sock.recv(256)
            if len(response) >= 8:
                status = struct.unpack('<I', response[4:8])[0]
                if status == 1:
                    print("[+] Registration successful")
                    return True
                else:
                    print(f"[!] Registration failed (status={status})")
                    return False

        except Exception as e:
            print(f"[!] Registration error: {e}")
            return False
        finally:
            reg_sock.close()

        return False

    def login(self) -> bool:
        """Login to V380 cloud server"""
        if not self.socket:
            if not self.connect():
                return False

        random_key = generate_random_key()
        encrypted_pass = encrypt_password(self.password, random_key)

        params = {
            "version": 31,
            "phoneType": 1012,
            "deviceId": self.device_id,
            "domain": self.domain,
            "port": self.stream_port,
            "accountId": 11,
            "username": str(self.device_id),
            "password": encrypted_pass,
            "randomKey": random_key,
            "connectType": 0,
            "securityLevel": 1,
            "agora": 0,
            "ectx": int(time.time()),
            "p2pIdx": 0
        }

        print("[*] Sending login request...")
        response = self._send_json_rpc("login", params)

        if response and 'v380' in response:
            v380 = response['v380']
            self.session = v380.get('session')
            self.handle = v380.get('handle')

            self.aes_key = generate_aes_key(self.handle)
            self.cipher = AES.new(self.aes_key, AES.MODE_ECB)

            print(f"[+] Login successful!")
            print(f"    Session: {self.session}")
            print(f"    Handle: {self.handle}")
            if self.debug:
                print(f"    AES Key: {self.aes_key.hex()}")

            if 'pri' in v380:
                pri = v380['pri']
                self.battery_level = pri.get('battery')
                self.audio_supported = pri.get('audio', 0) == 1
                print(f"    Battery: {self.battery_level}%")
                print(f"    Audio: {'supported' if self.audio_supported else 'not supported'}")

            return True

        print("[!] Login failed")
        if response and self.debug:
            print(f"    Response: {json.dumps(response, indent=2)}")
        return False

    def set_handle(self, handle: int):
        """Override encryption handle (for cameras with fixed handles)"""
        self.handle = handle
        self.aes_key = generate_aes_key(handle)
        self.cipher = AES.new(self.aes_key, AES.MODE_ECB)
        if self.debug:
            print(f"[*] Handle override: {handle}")
            print(f"    AES Key: {self.aes_key.hex()}")

    def _send_json_rpc(self, method: str, params: dict) -> dict:
        """Send JSON-RPC request to server"""
        payload = {
            "id": secrets.randbelow(100000000),
            "method": method,
            "params": params
        }

        json_bytes = json.dumps(payload, separators=(',', ':')).encode()
        length = len(json_bytes)
        packet = bytes([0x00, 0x03, 0x00, 0xfe]) + struct.pack('<H', length) + bytes([0x00, 0x00]) + json_bytes

        self.socket.sendall(packet)
        response = self.socket.recv(8192)

        try:
            text = response.decode('utf-8', errors='ignore')
            start = text.find('{')
            if start >= 0:
                depth = 0
                for i, c in enumerate(text[start:]):
                    if c == '{':
                        depth += 1
                    elif c == '}':
                        depth -= 1
                        if depth == 0:
                            result = json.loads(text[start:start+i+1])
                            if 'result' in result and 'code' in result.get('result', {}):
                                code = result['result']['code']
                                if code != 0:
                                    print(f"[!] Server error: {json.dumps(result['result'], indent=2)}")
                            return result
        except Exception as e:
            if self.debug:
                print(f"[!] Parse error: {e}")
        return {}

    def create_stream_socket(self) -> Optional[socket.socket]:
        """Create and authenticate a stream socket"""
        if not self.is_connected:
            print("[!] Not logged in")
            return None

        stream_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        stream_sock.settimeout(30)

        try:
            stream_sock.connect((self.server, self.stream_port))
            print(f"[+] Connected to stream port {self.stream_port}")

            # Send device request
            domain = self.domain.encode().ljust(48, b'\x00')
            packet = struct.pack('<II', 0x012d, 0x03ea) + domain
            packet += struct.pack('<HHH', 0x0000, 0x13ba, 0x0000)
            packet += struct.pack('<III', self.device_id, self.handle, self.session)
            packet = packet.ljust(256, b'\x00')
            stream_sock.sendall(packet)

            # Check response
            response = stream_sock.recv(256)
            if len(response) < 12 or response[:2] != b'\x91\x01':
                print(f"[!] Unexpected stream response")
                stream_sock.close()
                return None

            status = struct.unpack('<i', response[8:12])[0]
            if status != 4:
                print(f"[!] Stream authentication failed (status={status})")
                stream_sock.close()
                return None

            print("[+] Stream handshake successful")

            # Send init packet
            init_packet = bytes.fromhex("2f010000013000000000000000000000")
            init_packet += b'\x00' * (256 - len(init_packet))
            stream_sock.sendall(init_packet)

            # Send keepalive
            keepalive = bytes.fromhex("01210000000000000010000000000000")
            stream_sock.sendall(keepalive)

            return stream_sock

        except Exception as e:
            print(f"[!] Stream connection error: {e}")
            stream_sock.close()
            return None
