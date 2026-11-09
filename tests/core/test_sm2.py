"""sm2.py 单元测试: 国标向量, 加解密往返与确定性."""

import pytest

from shuimu.core import sm2

# GB/T 32905-2016 附录 A 标准向量
SM3_ABC = ("66c7f0f462eeedd9d1f2d46bdc10e4e2"
           "4167c4875cf2f7a2297da02b8f4ba8e0")
SM3_ABCD64 = ("debe9ff92275b8a138604889c18e5a4d"
              "6fdb70e5387e5765293dcba39c0c5732")

# 固定测试密钥对 (任意选的合法私钥, 公钥由曲线导出; 非真实凭据)
TEST_PRIVATE = 0x59276E27D506861F1667F6195360BA19C2FE3B1B3CD07FAF5FD3B1A0F1B2D7F3
K_FIXED = 0x1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF
K_ORDER_CHECK = 0x59276E27D506861F1667F6195360C2FE3B1B3CD07FAF5FD3B1F1B2D7F3A5B8

# 跨实现回归向量: 下述密文由本实现(固定 k)生成, 并经独立实现 gmssl 3.2.2
# (CryptSM2 mode=C1C3C2) 解密验证一致后固化; 此后回归不再依赖 gmssl.
XCHECK_PUBLIC = ("3d1420e1fbcc15c04743766fedaf7f530368710ebc4361e06a1289d48b2ed047"
                 "4142e6d5930c657d457f8052eaae52a2e497d1cf86f09033505b27d43f3e92c0")
XCHECK_CIPHER = ("3cbb3d1177ffa1a21bb054ae613455eb409a77593d17221808977ecbfb058647"
                 "9cf75393c8d257997f3fc1df9e952449746fd75ed1307e4dd493ae3dd0a800bd"
                 "9657b877059eedf524ab853a200427ddf3b192373632b08d85244e256c307d2f"
                 "e82a9446a77eb5445e0917d09b")
XCHECK_MESSAGE = b"pinned-vector"


def _public_hex(private: int) -> str:
    """由私钥导出公钥 hex (64 字节 X||Y)."""
    point = sm2._scalar_mul(private, sm2.G)  # pylint: disable=protected-access
    return (point[0].to_bytes(32, "big") + point[1].to_bytes(32, "big")).hex()


class TestSm3:
    """SM3 杂凑标准向量."""

    def test_abc(self):
        """GB/T 32905 向量 1: "abc"."""
        assert sm2.sm3(b"abc").hex() == SM3_ABC

    def test_64_bytes(self):
        """GB/T 32905 向量 2: "abcd" 重复 16 次 (512 位整块 + 填充块)."""
        assert sm2.sm3(b"abcd" * 16).hex() == SM3_ABCD64

    def test_empty(self):
        """空串输出 32 字节且稳定."""
        first = sm2.sm3(b"")
        assert len(first) == 32
        assert sm2.sm3(b"") == first

    def test_kdf_length(self):
        """KDF 输出长度精确, 跨 SM3 分组边界."""
        seed = bytes(range(64))
        for out_len in (1, 31, 32, 33, 64, 65, 100):
            assert len(sm2.sm3_kdf(seed, out_len)) == out_len


class TestPointOps:
    """曲线点运算自洽性."""

    def test_generator_on_curve(self):
        """基点在曲线上."""
        assert sm2._on_curve(sm2.G)  # pylint: disable=protected-access

    def test_scalar_mul_identity(self):
        """0*G = 无穷远, N*G = 无穷远, 1*G = G."""
        assert sm2._scalar_mul(0, sm2.G) is None  # pylint: disable=protected-access
        assert sm2._scalar_mul(sm2.N, sm2.G) is None  # pylint: disable=protected-access
        assert sm2._scalar_mul(1, sm2.G) == sm2.G  # pylint: disable=protected-access

    def test_add_commutative(self):
        """点加交换律 (含无穷远单位元)."""
        g = sm2.G
        g2 = sm2._scalar_mul(2, g)  # pylint: disable=protected-access
        g3 = sm2._scalar_mul(3, g)  # pylint: disable=protected-access
        assert sm2._point_add(g2, g3) == sm2._point_add(g3, g2)  # pylint: disable=protected-access
        assert sm2._point_add(None, g2) == g2  # pylint: disable=protected-access

    def test_public_key_derivation(self):
        """私钥导出公钥并在曲线上 (往返测试的前置)."""
        point = sm2._scalar_mul(TEST_PRIVATE, sm2.G)  # pylint: disable=protected-access
        assert point is not None
        assert sm2._on_curve(point)  # pylint: disable=protected-access


class TestEncryptDecrypt:
    """SM2 加解密."""

    def test_roundtrip_lengths(self):
        """多长度明文加解密往返, 密文长度 = 96 + 明文."""
        pub = _public_hex(TEST_PRIVATE)
        for message in (b"x", b"secret-password", bytes(31), bytes(32),
                        bytes(33), "中文密码测试".encode("utf-8"),
                        bytes(range(256))):
            cipher = sm2.sm2_encrypt(message, pub)
            assert len(cipher) == 2 * (96 + len(message))
            assert sm2.sm2_decrypt(cipher, TEST_PRIVATE) == message

    def test_deterministic_with_fixed_k(self):
        """同 k 同明文密文一致 (k 注入口可测试)."""
        pub = _public_hex(TEST_PRIVATE)
        first = sm2.sm2_encrypt(b"same", pub, rand_func=lambda: K_FIXED)
        second = sm2.sm2_encrypt(b"same", pub, rand_func=lambda: K_FIXED)
        assert first == second

    def test_cipher_is_c1c3c2(self):
        """密文序为 C1||C3||C2: 固定 k 下 C1 段可独立验证."""
        pub = _public_hex(TEST_PRIVATE)
        k = K_ORDER_CHECK
        c1 = sm2._scalar_mul(k, sm2.G)  # pylint: disable=protected-access
        c1_hex = (c1[0].to_bytes(32, "big")
                  + c1[1].to_bytes(32, "big")).hex()
        cipher = sm2.sm2_encrypt(b"order-check", pub, rand_func=lambda: k)
        assert cipher.startswith(c1_hex)
        # C3 = SM3(x2 || M || y2), 解密校验已覆盖; 再独立断言 C3 段
        shared = sm2._scalar_mul(k, sm2._parse_public_key(pub))  # pylint: disable=protected-access
        x2 = shared[0].to_bytes(32, "big")
        y2 = shared[1].to_bytes(32, "big")
        c3 = sm2.sm3(x2 + b"order-check" + y2).hex()
        assert cipher[128:128 + 64] == c3

    def test_tolerates_04_prefix_roundtrip(self):
        """加 04 前缀后解密仍成功 (服务端 04||C1C3C2 形态)."""
        pub = _public_hex(TEST_PRIVATE)
        cipher = sm2.sm2_encrypt(b"prefixed", pub)
        assert sm2.sm2_decrypt("04" + cipher, TEST_PRIVATE) == b"prefixed"

    def test_tampered_c3_rejected(self):
        """C3 被篡改时解密拒绝."""
        pub = _public_hex(TEST_PRIVATE)
        cipher = bytearray(bytes.fromhex(sm2.sm2_encrypt(b"data", pub)))
        cipher[64] ^= 0xFF
        with pytest.raises(ValueError, match="C3"):
            sm2.sm2_decrypt(cipher.hex(), TEST_PRIVATE)

    def test_invalid_public_key_rejected(self):
        """公钥长度非法/不在曲线上拒绝."""
        with pytest.raises(ValueError):
            sm2.sm2_encrypt(b"x", "abcd")
        off_curve = "00" * 64
        with pytest.raises(ValueError, match="曲线"):
            sm2.sm2_encrypt(b"x", off_curve)

    def test_cross_implementation_vector(self):
        """跨实现回归: 固定 k 的密文与经 gmssl 验证过的向量逐字节一致."""
        cipher = sm2.sm2_encrypt(XCHECK_MESSAGE, XCHECK_PUBLIC,
                                 rand_func=lambda: K_FIXED)
        assert cipher == XCHECK_CIPHER
        assert sm2.sm2_decrypt(XCHECK_CIPHER, TEST_PRIVATE) == XCHECK_MESSAGE
