# -*- coding: utf-8 -*-
"""
密码哈希处理 - 使用 Argon2
"""

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

# 创建密码哈希器
ph = PasswordHasher(
    time_cost=2,        # 迭代次数
    memory_cost=65536,  # 内存消耗 (64MB)
    parallelism=1,      # 并行度
    hash_len=32,        # 哈希长度
    salt_len=16         # 盐长度
)

def hash_password(password: str) -> str:
    """
    对密码进行哈希

    Args:
        password: 明文密码

    Returns:
        哈希后的密码字符串
    """
    return ph.hash(password)

def verify_password(password: str, password_hash: str) -> bool:
    """
    验证密码

    Args:
        password: 明文密码
        password_hash: 存储的哈希值

    Returns:
        密码是否匹配
    """
    try:
        ph.verify(password_hash, password)
        return True
    except VerifyMismatchError:
        return False

def needs_rehash(password_hash: str) -> bool:
    """
    检查密码是否需要重新哈希 (参数变更时)

    Args:
        password_hash: 存储的哈希值

    Returns:
        是否需要重新哈希
    """
    return ph.check_needs_rehash(password_hash)
