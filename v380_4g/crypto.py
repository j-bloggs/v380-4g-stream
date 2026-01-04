"""
V380 Cryptographic Functions

AES key generation and encryption/decryption routines.
"""

import struct
import base64
import secrets
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

# AES key derivation constants
MAGIC_1 = 0x618123462C14795C
MAGIC_2 = 0x82800DF0

# Static key for password encryption
V380_KEY = "macrovideo+*#!^@"


def generate_aes_key(handle: int) -> bytes:
    """Generate AES key from session handle"""
    handle_bytes = struct.pack("<I", handle)
    key = bytearray(handle_bytes.ljust(16, b"\x00")[:16])
    key[4:12] = struct.pack("<Q", MAGIC_1)
    key[12:16] = struct.pack("<I", MAGIC_2)
    return bytes(key)


def generate_random_key() -> str:
    """Generate 16-char random key for password encryption"""
    chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    return ''.join(secrets.choice(chars) for _ in range(16))


def encrypt_password(password: str, random_key: str) -> str:
    """Encrypt password for V380 authentication"""
    # First layer: encrypt with static V380 key
    padded1 = pad(password.encode(), 16)
    cipher1 = AES.new(V380_KEY.encode(), AES.MODE_ECB)
    encrypted1 = cipher1.encrypt(padded1)

    # Second layer: encrypt with random key
    padded2 = pad(encrypted1, 16)
    cipher2 = AES.new(random_key.encode(), AES.MODE_ECB)
    encrypted2 = cipher2.encrypt(padded2)

    return base64.b64encode(encrypted2).decode()


def decrypt_64_80(data: bytes, cipher: AES) -> bytes:
    """
    Decrypt using V380's 64/80 selective encryption pattern.

    Pattern: Decrypt 64 bytes, copy 16 bytes raw, repeat.
    Used for I-frames and P-frames >= 64 bytes.
    """
    result = bytearray()
    pos = 0

    while pos < len(data):
        remaining = len(data) - pos

        if remaining >= 64:
            # Decrypt 4 AES blocks (64 bytes)
            for i in range(4):
                block = data[pos + i*16 : pos + i*16 + 16]
                result.extend(cipher.decrypt(block))
            pos += 64

            # Copy up to 16 bytes raw
            raw_bytes = min(16, len(data) - pos)
            if raw_bytes > 0:
                result.extend(data[pos : pos + raw_bytes])
                pos += raw_bytes
        else:
            # Remaining bytes pass through raw
            result.extend(data[pos:])
            break

    return bytes(result)


def decrypt_audio(data: bytes, cipher: AES) -> bytes:
    """
    Decrypt audio using full AES-ECB.

    Unlike video, audio uses complete block encryption.
    """
    result = bytearray()

    # Decrypt complete 16-byte blocks
    for i in range(0, len(data) - 15, 16):
        block = data[i:i+16]
        result.extend(cipher.decrypt(block))

    # Pass through remaining bytes
    remaining = len(data) % 16
    if remaining:
        result.extend(data[-remaining:])

    return bytes(result)
