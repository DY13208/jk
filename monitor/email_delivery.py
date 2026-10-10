"""SMTP delivery using Python's standard email and smtplib components."""
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from .mail_templates import render_html


def recipients(value):
    addresses = [a.strip() for a in re.split('[,;；，]', value)]
    if not 1 <= len(addresses) <= 20 or any(not re.fullmatch(r'[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}', a) for a in addresses):
        raise ValueError('请填写有效收件邮箱；多个邮箱用逗号分隔，最多 20 个')
    return list(dict.fromkeys(addresses))


def configuration(site):
    host, sender = os.getenv('SMTP_HOST', '').strip(), os.getenv('SMTP_FROM', '').strip()
    if not host or not sender: raise ValueError('请在部署环境中配置 SMTP_HOST 和 SMTP_FROM')
    recipients(sender)
    if len(recipients(sender)) != 1: raise ValueError('SMTP_FROM 只能配置一个发件邮箱')
    mode = os.getenv('SMTP_SECURITY', 'ssl').lower()
    if mode not in ('ssl', 'starttls'): raise ValueError('SMTP_SECURITY 必须为 ssl 或 starttls')
    try: port = int(os.getenv('SMTP_PORT') or ('465' if mode == 'ssl' else '587'))
    except ValueError: raise ValueError('SMTP_PORT 必须是端口数字') from None
    if not 1 <= port <= 65535: raise ValueError('SMTP_PORT 超出有效范围')
    username, password = os.getenv('SMTP_USERNAME', ''), os.getenv('SMTP_PASSWORD', '')
    if bool(username) != bool(password): raise ValueError('SMTP_USERNAME 和 SMTP_PASSWORD 必须同时配置')
    return dict(host=host, port=port, mode=mode, sender=sender, username=username,
                password=password, recipients=recipients(site.get('email_to', '')))


def build_email(site, event, content, config):
    mail = EmailMessage()
    mail['From'] = config['sender']
    mail['To'] = ', '.join(config['recipients'])
    # Scraped names cannot inject mail headers.
    mail['Subject'] = content.splitlines()[0][:180]
    mail['Message-ID'] = f"<product-monitor-event-{event['id']}@{config['sender'].split('@')[1]}>"
    mail.set_content(content)
    mail.add_alternative(render_html(site,event),subtype='html')
    return mail


def send_email(config, mail):
    context = ssl.create_default_context()
    client = (smtplib.SMTP_SSL(config['host'], config['port'], timeout=15, context=context)
              if config['mode'] == 'ssl' else smtplib.SMTP(config['host'], config['port'], timeout=15))
    try:
        if config['mode'] == 'starttls':
            client.ehlo(); client.starttls(context=context); client.ehlo()
        if config['username']: client.login(config['username'], config['password'])
        return client.send_message(mail, from_addr=config['sender'], to_addrs=config['recipients'])
    finally:
        # QUIT failure after successful DATA must not downgrade an accepted delivery.
        try: client.quit()
        except Exception: client.close()
