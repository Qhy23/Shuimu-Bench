"""国密 SM2/SM3 纯 Python 实现 (仅标准库, 供校园统一认证登录加密密码).

SM3 按 GB/T 32905-2016, SM2 加密按 GB/T 32918 密码算法 (sm2p256v1 曲线,
密文序 C1||C3||C2, 与服务端期望的 sm-crypto C1C3C2 模式一致; 输出不含
未压缩点 04 前缀, 由调用方自行拼接). 逐字段对国标公开测试向量验证,
见 tests/core/test_sm2.py.

实现边界: 非常数时间 (登录场景为客户端加密公钥明文, 无侧信道防护需求);
仿射坐标点乘 (一次登录仅 2 次点乘, 毫秒级, 不做窗口优化).
"""

import secrets
import struct

# ---- SM2 推荐曲线 sm2p256v1 参数 (GB/T 32918.5-2017) ----

P = 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF00000000FFFFFFFFFFFFFFFF
A = P - 3
B = 0x28E9FA9E9D9F5E344D5A9E4BCF6509A7F39789F515AB8F92DDBCBD414D940E93
N = 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFF7203DF6B21C6052B53BBF40939D54123
GX = 0x32C4AE2C1F1981195F9904466A39C9948FE30BBFF2660BE1715A4589334C74C7
GY = 0xBC3736A2F4F6779C59BDCEE36B692153D0A9877CC62A474002DF32E52139F0A0
G = (GX, GY)

COORD_BYTES = 32   # 坐标大端字节数
SM3_LEN = 32       # SM3 输出字节数


# ---- SM3 杂凑 (GB/T 32905-2016) ----

_IV = (0x7380166F, 0x4914B2B9, 0x172442D7, 0xDA8A0600,
       0xA96F30BC, 0x163138AA, 0xE38DEE4D, 0xB0FB0E4E)


def _rotl(x: int, n: int) -> int:
    """32 位循环左移."""
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF


def sm3(data: bytes) -> bytes:
    """SM3 杂凑, 输入任意字节串, 输出 32 字节."""
    msg = bytearray(data)
    msg.append(0x80)
    while len(msg) % 64 != 56:
        msg.append(0)
    msg += (len(data) * 8).to_bytes(8, "big")
    v = list(_IV)
    for start in range(0, len(msg), 64):
        block = msg[start:start + 64]
        w = list(struct.unpack(">16I", block))
        for j in range(16, 68):
            x = w[j - 16] ^ w[j - 9] ^ _rotl(w[j - 3], 15)
            x = x ^ _rotl(x, 15) ^ _rotl(x, 23)          # P1
            w.append(x ^ _rotl(w[j - 13], 7) ^ w[j - 6])
        w_prime = [w[j] ^ w[j + 4] for j in range(64)]
        a, b, c, d, e, f, g, h = v
        for j in range(64):
            t = 0x79CC4519 if j < 16 else 0x7A879D8A
            ss1 = _rotl(
                (_rotl(a, 12) + e + _rotl(t, j % 32)) & 0xFFFFFFFF, 7)
            ss2 = ss1 ^ _rotl(a, 12)
            if j < 16:
                ff = a ^ b ^ c
                gg = e ^ f ^ g
            else:
                ff = (a & b) | (a & c) | (b & c)
                gg = (e & f) | (~e & 0xFFFFFFFF & g)
            tt1 = (ff + d + ss2 + w_prime[j]) & 0xFFFFFFFF
            tt2 = (gg + h + ss1 + w[j]) & 0xFFFFFFFF
            d = c
            c = _rotl(b, 9)
            b = a
            a = tt1
            h = g
            g = _rotl(f, 19)
            f = e
            e = tt2 ^ _rotl(tt2, 9) ^ _rotl(tt2, 17)     # P0
        v = [x ^ y for x, y in zip(v, (a, b, c, d, e, f, g, h))]
    return struct.pack(">8I", *v)


def sm3_kdf(z: bytes, out_len: int) -> bytes:
    """密钥派生函数 (GB/T 32918.4): SM3 计数器模式, out_len 为字节数."""
    out = bytearray()
    counter = 1
    while len(out) < out_len:
        out += sm3(z + counter.to_bytes(4, "big"))
        counter += 1
    return bytes(out[:out_len])


# ---- 椭圆曲线点运算 (仿射坐标, 无穷远点用 None 表示) ----

def _on_curve(point: tuple) -> bool:
    """点是否在 sm2p256v1 上."""
    x, y = point
    return (y * y - x * x * x - A * x - B) % P == 0


def _point_add(p1: tuple, p2: tuple):
    """点加; 含倍点 (同点时走倍点公式)."""
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2:
        if (y1 + y2) % P == 0:
            return None
        slope = (3 * x1 * x1 + A) * pow(2 * y1, -1, P) % P
    else:
        slope = (y2 - y1) * pow(x2 - x1, -1, P) % P
    x3 = (slope * slope - x1 - x2) % P
    y3 = (slope * (x1 - x3) - y1) % P
    return (x3, y3)


def _point_double(point: tuple):
    """倍点."""
    return _point_add(point, point)


def _scalar_mul(k: int, point: tuple):
    """标量乘 (倍加法, 从低位起)."""
    result = None
    addend = point
    while k:
        if k & 1:
            result = _point_add(result, addend)
        addend = _point_double(addend)
        k >>= 1
    return result


# ---- SM2 加解密 (GB/T 32918.4, 密文序 C1||C3||C2) ----

def _parse_public_key(public_key_hex: str) -> tuple:
    """解析 128 hex 公钥 (容忍 04 前缀), 返回曲线点."""
    text = public_key_hex.strip().lower()
    if len(text) == 130 and text.startswith("04"):
        text = text[2:]
    if len(text) != 128:
        raise ValueError("公钥长度非法(须为 64 字节 hex)")
    point = (int(text[:64], 16), int(text[64:], 16))
    if not _on_curve(point):
        raise ValueError("公钥不在曲线上")
    return point


def _random_k() -> int:
    """随机数 k in [1, N-1]."""
    return secrets.randbelow(N - 1) + 1


def sm2_encrypt(plaintext: bytes, public_key_hex: str,
                rand_func=None) -> str:
    """SM2 加密, 返回 hex 字符串 (C1||C3||C2, 不含 04 前缀).

    rand_func 返回 k (int), 供测试注入固定随机数; 缺省用 secrets.
    t 全零按标准要求重取 k.
    """
    public = _parse_public_key(public_key_hex)
    while True:
        k = rand_func() if rand_func is not None else _random_k()
        c1 = _scalar_mul(k, G)
        shared = _scalar_mul(k, public)
        if shared is None:
            continue
        x2 = shared[0].to_bytes(COORD_BYTES, "big")
        y2 = shared[1].to_bytes(COORD_BYTES, "big")
        t = sm3_kdf(x2 + y2, len(plaintext))
        if not plaintext or any(t):
            break
    c2 = bytes(a ^ b for a, b in zip(plaintext, t))
    c3 = sm3(x2 + plaintext + y2)
    c1_bytes = (c1[0].to_bytes(COORD_BYTES, "big")
                + c1[1].to_bytes(COORD_BYTES, "big"))
    return (c1_bytes + c3 + c2).hex()


def _decrypt_data(data: bytes, private_key: int) -> bytes:
    """按无前缀格式 (C1||C3||C2) 解密, 任何校验失败抛 ValueError."""
    if len(data) < 2 * COORD_BYTES + SM3_LEN:
        raise ValueError("密文过短")
    c1 = (int.from_bytes(data[:COORD_BYTES], "big"),
          int.from_bytes(data[COORD_BYTES:2 * COORD_BYTES], "big"))
    if not _on_curve(c1):
        raise ValueError("C1 不在曲线上")
    c3 = data[2 * COORD_BYTES:2 * COORD_BYTES + SM3_LEN]
    c2 = data[2 * COORD_BYTES + SM3_LEN:]
    shared = _scalar_mul(private_key, c1)
    x2 = shared[0].to_bytes(COORD_BYTES, "big")
    y2 = shared[1].to_bytes(COORD_BYTES, "big")
    t = sm3_kdf(x2 + y2, len(c2))
    plaintext = bytes(a ^ b for a, b in zip(c2, t))
    if sm3(x2 + plaintext + y2) != c3:
        raise ValueError("C3 校验失败")
    return plaintext


def sm2_decrypt(cipher_hex: str, private_key: int) -> bytes:
    """SM2 解密 (C1||C3||C2, 容忍 04 前缀); C3 不符抛 ValueError.

    前缀歧义: C1 的 x 坐标首字节本有 ~1/256 概率恰为 0x04, 不能凭首
    字节判定有无前缀(曾致解密偶发"C1 不在曲线上"); 先按无前缀解析,
    失败且确实带 04 前缀时再剥一位重试. 登录流程不需要解密, 供测试
    往返与将来扩展.
    """
    data = bytes.fromhex(cipher_hex)
    try:
        return _decrypt_data(data, private_key)
    except ValueError:
        if len(data) >= 1 + 2 * COORD_BYTES + SM3_LEN and data[0] == 0x04:
            return _decrypt_data(data[1:], private_key)
        raise
