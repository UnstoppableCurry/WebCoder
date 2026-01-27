# -*- coding: utf-8 -*-
"""
邮件服务
"""

import smtplib
import asyncio
import random
import string
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    EMAIL_ENABLED,
    EMAIL_SMTP_SERVER,
    EMAIL_SMTP_PORT,
    EMAIL_SENDER,
    EMAIL_PASSWORD,
    VERIFICATION_CODE_LENGTH
)

def generate_verification_code(length: int = VERIFICATION_CODE_LENGTH) -> str:
    """生成验证码"""
    return ''.join(random.choices(string.digits, k=length))

def _send_email_sync(to: str, subject: str, content: str, html: bool = False, retry: int = 2):
    """
    同步发送邮件（带重试机制）

    Args:
        to: 收件人
        subject: 主题
        content: 内容
        html: 是否HTML
        retry: 重试次数
    """
    if not EMAIL_ENABLED:
        print(f"[邮件] 邮件服务未启用，跳过发送: {subject}")
        # 开发模式下返回 True，避免阻止注册流程
        return True

    if not EMAIL_SENDER or not EMAIL_PASSWORD:
        print(f"[邮件] 邮箱配置不完整，跳过发送")
        return False

    last_error = None
    for attempt in range(retry + 1):
        try:
            msg = MIMEMultipart()
            msg['From'] = EMAIL_SENDER
            msg['To'] = to
            msg['Subject'] = subject

            if html:
                msg.attach(MIMEText(content, 'html', 'utf-8'))
            else:
                msg.attach(MIMEText(content, 'plain', 'utf-8'))

            server = smtplib.SMTP_SSL(EMAIL_SMTP_SERVER, EMAIL_SMTP_PORT, timeout=10)
            try:
                server.login(EMAIL_SENDER, EMAIL_PASSWORD)
                server.sendmail(EMAIL_SENDER, to, msg.as_string())
                print(f"[邮件] 发送成功: {to} - {subject}")
                return True
            finally:
                try:
                    server.quit()
                except:
                    pass  # 忽略关闭连接时的异常

        except smtplib.SMTPAuthenticationError as e:
            print(f"[邮件] 认证失败（请检查邮箱账号和授权密码）: {e}")
            return False  # 认证错误不重试

        except smtplib.SMTPRecipientsRefused as e:
            print(f"[邮件] 收件人被拒绝（邮箱地址可能无效）: {to}")
            return False  # 收件人错误不重试

        except Exception as e:
            last_error = e
            if attempt < retry:
                print(f"[邮件] 发送失败，重试 {attempt + 1}/{retry}: {e}")
                import time
                time.sleep(1)  # 等待1秒后重试
            else:
                print(f"[邮件] 发送失败（已重试{retry}次）: {e}")

    return False

async def send_email(to: str, subject: str, content: str, html: bool = False) -> bool:
    """
    异步发送邮件

    Args:
        to: 收件人邮箱
        subject: 邮件主题
        content: 邮件内容
        html: 是否为 HTML 格式

    Returns:
        是否发送成功
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _send_email_sync, to, subject, content, html)

async def send_verification_code(to: str, code: str, purpose: str) -> bool:
    """
    发送验证码邮件

    Args:
        to: 收件人邮箱
        code: 验证码
        purpose: 用途 ('register', 'reset_password', 'login')

    Returns:
        是否发送成功
    """
    purpose_text = {
        'register': '注册账号',
        'reset_password': '重置密码',
        'login': '登录验证'
    }.get(purpose, '验证')

    subject = f"Claude Mobile - {purpose_text}验证码"

    content = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #333;">Claude Mobile 验证码</h2>
        <p>您好！</p>
        <p>您正在进行<strong>{purpose_text}</strong>操作，验证码为：</p>
        <div style="background: #f5f5f5; padding: 20px; text-align: center; margin: 20px 0;">
            <span style="font-size: 32px; font-weight: bold; letter-spacing: 8px; color: #0066ff;">{code}</span>
        </div>
        <p style="color: #666;">验证码有效期为 10 分钟，请勿泄露给他人。</p>
        <p style="color: #999; font-size: 12px;">如果这不是您本人的操作，请忽略此邮件。</p>
    </div>
    """

    return await send_email(to, subject, content, html=True)

async def send_notification_email(to: str, title: str, content: str) -> bool:
    """
    发送通知邮件

    Args:
        to: 收件人邮箱
        title: 通知标题
        content: 通知内容

    Returns:
        是否发送成功
    """
    subject = f"Claude Mobile - {title}"

    html_content = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <h2 style="color: #333;">{title}</h2>
        <div style="background: #f5f5f5; padding: 20px; margin: 20px 0; white-space: pre-wrap;">
{content}
        </div>
        <p style="color: #999; font-size: 12px;">
            此邮件由 Claude Mobile 自动发送。
        </p>
    </div>
    """

    return await send_email(to, subject, html_content, html=True)
