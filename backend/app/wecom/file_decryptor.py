from __future__ import annotations

import base64

try:
    from Crypto.Cipher import AES
    WECOM_AES_IMPORT_ERROR: ImportError | None = None
except ImportError as exc:
    AES = None
    WECOM_AES_IMPORT_ERROR = exc


def decrypt_wecom_file_bytes_with_key(encrypted_bytes: bytes, encoding_aes_key: str) -> bytes:
    if AES is None:
        raise RuntimeError("文件解密依赖缺失: 请安装 pycryptodome") from WECOM_AES_IMPORT_ERROR
    if not encrypted_bytes:
        raise RuntimeError("文件解密失败: 密文内容为空")

    aes_key = _decode_encoding_aes_key(encoding_aes_key)
    if len(encrypted_bytes) % AES.block_size != 0:
        raise RuntimeError(
            "文件解密失败: 密文长度不是 AES block size 的整数倍"
        )

    cipher = AES.new(aes_key, AES.MODE_CBC, aes_key[:16])
    try:
        decrypted = cipher.decrypt(encrypted_bytes)
    except ValueError as exc:
        raise RuntimeError(f"文件解密失败: {exc}") from exc

    plain_bytes = _pkcs7_unpad(decrypted)
    if not plain_bytes:
        raise RuntimeError("文件解密失败: 明文内容为空")
    return plain_bytes


def validate_decrypted_xlsx_bytes(file_bytes: bytes) -> None:
    if len(file_bytes) < 4:
        raise RuntimeError("文件解密失败: 解密后的文件内容过短")
    if file_bytes[:4] != b"PK\x03\x04":
        raise RuntimeError("文件解密失败: 解密后的内容不是合法 xlsx(zip) 文件")


def _decode_encoding_aes_key(raw_key: str) -> bytes:
    key = raw_key.strip()
    if not key:
        raise RuntimeError("文件解密失败: 未提供文件消息中的 AESKey")

    try:
        decoded = base64.b64decode(f"{key}=", validate=True)
    except Exception as exc:
        raise RuntimeError("文件解密失败: 文件消息中的 AESKey 不是合法的 base64 字符串") from exc

    if len(decoded) != 32:
        raise RuntimeError("文件解密失败: 文件消息中的 AESKey 解码后长度不是 32 字节")
    return decoded


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        raise RuntimeError("文件解密失败: 解密结果为空")

    pad = data[-1]
    if pad < 1 or pad > 32:
        raise RuntimeError("文件解密失败: PKCS#7 padding 非法")
    if len(data) < pad:
        raise RuntimeError("文件解密失败: PKCS#7 padding 长度非法")
    if data[-pad:] != bytes([pad]) * pad:
        raise RuntimeError("文件解密失败: PKCS#7 padding 校验失败")
    return data[:-pad]
